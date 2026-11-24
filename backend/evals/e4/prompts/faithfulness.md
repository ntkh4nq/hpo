# Task

You check the "what the literature says" sentences of a prenatal lookup assistant. Each numbered item has a disease, a phenotype, one sentence written in Vietnamese, and the passages (in English) the sentence cites. Decide whether the cited passages support the sentence.

# Rules

Answer `supported = true` only when the cited passages state what the sentence says about that phenotype in that disease: people or fetuses with that very disease have the phenotype (or the more specific form the sentence names), with any detail the sentence adds (frequency, timing, severity, prenatal detection) also present in the passages.

Answer `false` when:
- the sentence adds a fact the passages do not state;
- the passages are about another disease or entity, a differential diagnosis, or an animal model only;
- the phenotype appears in the passages only under treatment, management, surveillance or follow-up, or as an item of a scoring scale or a test to perform;
- the passages name only a nearby sign that the phenotype does not cover.

Judge only from the words of the passages. Read the Vietnamese sentence for its meaning; do not judge its style.

# Output

Return `judgements`: one entry per item, `index` = the item number, `supported` = true or false.
