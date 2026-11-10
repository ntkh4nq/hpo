"""Seed KB layers 1–2 from one pinned HPO release plus the Vietnamese translation sheet.

    python -m kb.seed download [TAG]   # fetch release files (default: latest) into data/raw/hpo/<TAG>/
    python -m kb.seed load [TAG]       # (re)build layers 1–2 (default: newest release on disk)

`load` runs in one transaction and is idempotent: hpo_term/hpo_name are synced by key (so
embeddings survive a re-run), every other table is rebuilt from the files.
"""

import csv
import hashlib
import json
import re
import shutil
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

from kb.db import connect

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
HPO_REPO = "obophenotype/human-phenotype-ontology"
HPO_FILES = ("hp.json", "phenotype.hpoa", "genes_to_disease.txt")
HPO_LICENSE = "https://hpo.jax.org/license"
TRANSLATIONS = RAW / "translations" / "HPO_review_doctor_full.xlsx"
TRANSLATION_COLUMNS = {"Bản dịch bác sĩ duyệt": "doctor", "Claude": "claude", "ChatGPT": "chatgpt"}
PRENATAL_RULES = Path(__file__).with_name("prenatal_rules.csv")

HP_PREFIX = "http://purl.obolibrary.org/obo/HP_"
REPLACED_BY = "http://purl.obolibrary.org/obo/IAO_0100001"
ALT_ID = "http://www.geneontology.org/formats/oboInOwl#hasAlternativeId"
SYNONYM_KIND = {
    "hasExactSynonym": "exact_syn",
    "hasRelatedSynonym": "related_syn",
    "hasBroadSynonym": "broad_syn",
    "hasNarrowSynonym": "narrow_syn",
}


# ---------- download ----------

def _github(url: str, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "hpo-kb-seed"})
    return urllib.request.urlopen(request, timeout=600)


def download(tag: str | None = None) -> Path:
    endpoint = f"tags/{tag}" if tag else "latest"
    release = json.load(_github(f"https://api.github.com/repos/{HPO_REPO}/releases/{endpoint}"))
    out = RAW / "hpo" / release["tag_name"]
    out.mkdir(parents=True, exist_ok=True)
    for asset in release["assets"]:
        if asset["name"] not in HPO_FILES:
            continue
        target = out / asset["name"]
        # browser_download_url keeps answering 504; the API asset endpoint serves the same bytes.
        with _github(asset["url"], "application/octet-stream") as response, target.open("wb") as f:
            shutil.copyfileobj(response, f)
        if target.stat().st_size != asset["size"]:
            raise RuntimeError(f"{target}: got {target.stat().st_size} bytes, expected {asset['size']}")
        print(f"downloaded {target}")
    return out


# ---------- parsing ----------

def norm(text: str) -> str:
    """Lowercase, strip Vietnamese diacritics, collapse spaces — the key for trigram search."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def hp_id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1].replace("_", ":")


def parse_hp(path: Path):
    graph = json.loads(path.read_text())["graphs"][0]
    terms, names, alt_ids = [], [], {}
    for node in graph["nodes"]:
        if not node["id"].startswith(HP_PREFIX) or node.get("type") != "CLASS":
            continue
        term_id, meta = hp_id(node["id"]), node.get("meta", {})
        props = meta.get("basicPropertyValues", [])
        obsolete = bool(meta.get("deprecated"))
        definition = meta.get("definition", {}).get("val")
        replaced_by = next((p["val"] for p in props if p["pred"] == REPLACED_BY), None)
        terms.append((term_id, node.get("lbl") or term_id, definition, obsolete, replaced_by))
        alt_ids.update((p["val"], term_id) for p in props if p["pred"] == ALT_ID)
        if obsolete:
            continue  # obsolete terms stay resolvable but are never searchable
        names.append((term_id, node["lbl"], "en", "label", "hpo"))
        names += [(term_id, s["val"], "en", SYNONYM_KIND[s["pred"]], "hpo") for s in meta.get("synonyms", [])]
        if definition:
            names.append((term_id, definition, "en", "definition", "hpo"))
    edges = {
        (hp_id(e["sub"]), hp_id(e["obj"]))
        for e in graph["edges"]
        if e["pred"] == "is_a" and e["sub"].startswith(HP_PREFIX) and e["obj"].startswith(HP_PREFIX)
    }
    version = graph.get("meta", {}).get("version", "")
    return terms, names, sorted(edges), alt_ids, version


def parse_translations(path: Path, current: set[str]):
    """Only rows whose ID is a current term. Alt IDs are NOT followed: HPO lists merged obsolete
    IDs as alt IDs, and the sheet's text for those translates the obsolete label ('[obsolete] ...')."""
    rows = openpyxl.load_workbook(path, read_only=True).active.iter_rows(values_only=True)
    header = next(rows)
    id_col = header.index("HPO_ID")
    text_cols = {header.index(column): source for column, source in TRANSLATION_COLUMNS.items()}
    names, unresolved = [], []
    for row in rows:
        term_id = str(row[id_col] or "").strip()
        if term_id not in current:
            unresolved.append(row[id_col])
            continue
        for col, source in text_cols.items():
            if text := str(row[col] or "").strip():
                names.append((term_id, text, "vi", "translation", source))
    return names, unresolved


def parse_frequency(raw: str):
    """-> (num, den, hpo, pct). Unknown formats fail loudly: the release changed shape."""
    if not raw:
        return None, None, None, None
    if raw.startswith("HP:"):
        return None, None, raw, None
    if m := re.fullmatch(r"(\d+)/(\d+)", raw):
        return int(m[1]), int(m[2]), None, None
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)%", raw):
        return None, None, None, float(m[1])
    raise ValueError(f"unknown frequency format: {raw!r}")


def _split(value: str) -> list[str] | None:
    return [v.strip() for v in value.split(";") if v.strip()] or None


def parse_hpoa(path: Path, resolve):
    lines = path.read_text().splitlines()
    meta = dict(l[1:].split(": ", 1) for l in lines if l.startswith("#") and ": " in l)
    diseases, annotations, unresolved = {}, [], []
    for r in csv.DictReader((l for l in lines if not l.startswith("#")), delimiter="\t"):
        term_id = resolve(r["hpo_id"])
        if term_id is None:
            unresolved.append(r["hpo_id"])
            continue
        disease_id = r["database_id"]
        diseases[disease_id] = (disease_id, r["disease_name"], disease_id.split(":", 1)[0])
        annotations.append((
            disease_id, term_id, r["qualifier"] == "NOT", r["frequency"] or None,
            *parse_frequency(r["frequency"]), r["onset"] or None, r["sex"] or None,
            _split(r["modifier"]), r["aspect"], r["evidence"] or None, _split(r["reference"]),
            r["biocuration"] or None,
        ))
    return meta, diseases, annotations, unresolved


def parse_genes(path: Path, diseases: dict):
    genes, links = {}, {}
    with path.open() as f:
        for r in csv.DictReader(f, delimiter="\t"):
            genes[r["ncbi_gene_id"]] = (r["ncbi_gene_id"], r["gene_symbol"])
            links.setdefault((r["ncbi_gene_id"], r["disease_id"]),
                             (r["ncbi_gene_id"], r["disease_id"], r["association_type"], r["source"]))
            diseases.setdefault(r["disease_id"], (r["disease_id"], None, r["disease_id"].split(":", 1)[0]))
    return list(genes.values()), list(links.values())


# ---------- loading ----------

def _copy(cur, table: str, cols: tuple[str, ...], rows) -> None:
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _sync(cur, table: str, key: tuple[str, ...], cols: tuple[str, ...], rows) -> None:
    """Make `table` hold exactly `rows` (matched on `key`), leaving columns outside `cols` untouched."""
    col_list, key_list = ", ".join(cols), ", ".join(key)
    cur.execute(f"CREATE TEMP TABLE incoming AS SELECT {col_list} FROM {table} WITH NO DATA")
    _copy(cur, "incoming", cols, rows)
    cur.execute(f"DELETE FROM {table} t WHERE NOT EXISTS "
                f"(SELECT 1 FROM incoming i WHERE ({', '.join('i.' + k for k in key)}) = "
                f"({', '.join('t.' + k for k in key)}))")
    rest = [c for c in cols if c not in key]
    cur.execute(
        f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM incoming "
        f"ON CONFLICT ({key_list}) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in rest)} "
        f"WHERE ({', '.join(f'{table}.{c}' for c in rest)}) IS DISTINCT FROM "
        f"({', '.join(f'EXCLUDED.{c}' for c in rest)})"
    )
    cur.execute("DROP TABLE incoming")


def _manifest_row(source: str, release: str, path: Path, url: str | None, license_: str | None):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return source, release, path.name, url, sha, mtime, license_


def load(tag: str | None = None) -> None:
    release_dir = RAW / "hpo" / tag if tag else max((RAW / "hpo").iterdir())
    tag = release_dir.name

    terms, names, edges, alt_ids, hp_version = parse_hp(release_dir / "hp.json")
    if tag.lstrip("v") not in hp_version:
        raise RuntimeError(f"hp.json version {hp_version!r} does not match release {tag}")
    current = {t[0] for t in terms if not t[3]}

    def resolve(raw_id) -> str | None:  # annotations follow alt IDs (merged terms); replaced_by is not followed
        term_id = str(raw_id or "").strip()
        term_id = alt_ids.get(term_id, term_id)
        return term_id if term_id in current else None

    vi_names, vi_unresolved = parse_translations(TRANSLATIONS, current)
    hpoa_meta, diseases, annotations, hpoa_unresolved = parse_hpoa(release_dir / "phenotype.hpoa", resolve)
    if tag.lstrip("v") not in hpoa_meta.get("hpo-version", ""):
        raise RuntimeError(f"phenotype.hpoa built on {hpoa_meta.get('hpo-version')!r}, not {tag}")
    genes, gene_links = parse_genes(release_dir / "genes_to_disease.txt", diseases)
    all_names = list(dict.fromkeys(
        (term_id, text, lang, kind, source, norm(text)) for term_id, text, lang, kind, source in names + vi_names
    ))
    with PRENATAL_RULES.open() as f:
        rules = list(csv.DictReader(f))

    with connect() as conn, conn.cursor() as cur:
        _sync(cur, "hpo_term", ("hpo_id",),
              ("hpo_id", "label_en", "definition_en", "is_obsolete", "replaced_by"), terms)
        cur.execute("TRUNCATE hpo_edge, hpo_alt_id, hpo_ancestor, disease, disease_phenotype, gene, gene_disease")
        _copy(cur, "hpo_edge", ("child_id", "parent_id"), edges)
        _copy(cur, "hpo_alt_id", ("alt_id", "hpo_id"), ((a, t) for a, t in alt_ids.items() if a not in current))
        _sync(cur, "hpo_name", ("hpo_id", "lang", "kind", "source", "text"),
              ("hpo_id", "text", "lang", "kind", "source", "text_norm"), all_names)

        cur.execute("""
            INSERT INTO hpo_ancestor (hpo_id, ancestor_id, distance)
            WITH RECURSIVE walk (hpo_id, ancestor_id, distance) AS (
                SELECT hpo_id, hpo_id, 0 FROM hpo_term WHERE NOT is_obsolete
                UNION
                SELECT w.hpo_id, e.parent_id, w.distance + 1 FROM walk w JOIN hpo_edge e ON e.child_id = w.ancestor_id
            )
            SELECT hpo_id, ancestor_id, min(distance) FROM walk GROUP BY hpo_id, ancestor_id""")

        _copy(cur, "disease", ("disease_id", "name", "db"), diseases.values())
        _copy(cur, "disease_phenotype", (
            "disease_id", "hpo_id", "is_negated", "freq_raw", "freq_num", "freq_den", "freq_hpo", "freq_pct",
            "onset_hpo", "sex", "modifiers", "aspect", "evidence", '"references"', "biocuration"), annotations)
        _copy(cur, "gene", ("ncbi_gene_id", "symbol"), genes)
        _copy(cur, "gene_disease", ("ncbi_gene_id", "disease_id", "association_type", "source"), gene_links)

        # IC(t) = -ln(diseases annotated with t or a descendant / all annotated diseases); NOT rows excluded.
        cur.execute("UPDATE hpo_term SET ic = NULL")
        cur.execute("""
            WITH positive AS (SELECT disease_id, hpo_id FROM disease_phenotype WHERE NOT is_negated AND aspect = 'P'),
                 total AS (SELECT count(DISTINCT disease_id)::float AS n FROM positive),
                 per_term AS (SELECT a.ancestor_id AS hpo_id, count(DISTINCT p.disease_id) AS k
                              FROM positive p JOIN hpo_ancestor a ON a.hpo_id = p.hpo_id GROUP BY a.ancestor_id)
            UPDATE hpo_term t SET ic = -ln(per_term.k / total.n) FROM per_term, total WHERE t.hpo_id = per_term.hpo_id""")

        # prenatal flag: every rule is data (prenatal_rules.csv); each flagged term records which rules hit it
        rule_ids = [r["hpo_id"] for r in rules]
        labels = dict(cur.execute("SELECT hpo_id, label_en FROM hpo_term WHERE hpo_id = ANY(%s) AND NOT is_obsolete",
                                  (rule_ids,)).fetchall())
        wrong = [(r["hpo_id"], r["label"], labels.get(r["hpo_id"])) for r in rules if labels.get(r["hpo_id"]) != r["label"]]
        if wrong or {r["kind"] for r in rules} - {"subtree", "onset"}:
            raise RuntimeError(f"prenatal_rules.csv does not match this release or has an unknown kind: {wrong}")
        cur.execute("UPDATE hpo_term SET prenatal_relevant = false, prenatal_basis = NULL")
        cur.execute("""
            WITH rule (kind, rule_id) AS (SELECT * FROM unnest(%s::text[], %s::text[])),
            hit AS (
                -- subtree: the term is the rule term or one of its descendants
                SELECT a.hpo_id, 'subtree:' || r.rule_id AS basis
                FROM rule r JOIN hpo_ancestor a ON a.ancestor_id = r.rule_id WHERE r.kind = 'subtree'
                UNION
                -- onset: some disease is annotated with the term, with an onset under the rule term
                SELECT dp.hpo_id, 'onset:' || r.rule_id
                FROM rule r JOIN hpo_ancestor o ON o.ancestor_id = r.rule_id
                JOIN disease_phenotype dp ON dp.onset_hpo = o.hpo_id
                WHERE r.kind = 'onset' AND NOT dp.is_negated
            )
            UPDATE hpo_term t SET prenatal_relevant = true, prenatal_basis = b.basis
            FROM (SELECT hpo_id, string_agg(basis, ';' ORDER BY basis) AS basis FROM hit GROUP BY hpo_id) b
            WHERE t.hpo_id = b.hpo_id""", ([r["kind"] for r in rules], rule_ids))

        url = f"https://github.com/{HPO_REPO}/releases/download/{tag}/"
        manifest = [_manifest_row("hpo", tag, release_dir / f, url + f, HPO_LICENSE) for f in HPO_FILES]
        manifest.append(_manifest_row("translations", "gvhd", TRANSLATIONS, None, None))
        cur.execute("DELETE FROM kb_manifest WHERE (source, release) IN (('hpo', %s), ('translations', 'gvhd'))", (tag,))
        _copy(cur, "kb_manifest", ("source", "release", "file", "url", "sha256", "downloaded_at", "license"), manifest)

        counts = {t: cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in (
            "hpo_term", "hpo_name", "hpo_edge", "hpo_ancestor", "hpo_alt_id",
            "disease", "disease_phenotype", "gene", "gene_disease")}
        prenatal = cur.execute("SELECT count(*) FROM hpo_term WHERE prenatal_relevant").fetchone()[0]

    print(f"release {tag}: {counts}; prenatal_relevant={prenatal}")
    print(f"skipped: {len(vi_unresolved)} translation rows (obsolete/unknown id), "
          f"{len(hpoa_unresolved)} hpoa rows (unknown id): {sorted(set(map(str, hpoa_unresolved)))[:10]}")


if __name__ == "__main__":
    command, *args = sys.argv[1:] or ["load"]
    {"download": download, "load": load}[command](*args)
