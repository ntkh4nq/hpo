"""Appendix C.1 rules on hand-written JATS snippets."""

import xml.etree.ElementTree as ET
from pathlib import Path

from kb.ingest import jats
from kb.ingest.model import Figure, Paragraph, Table


def read(body_xml: str, skip=("References",)):
    body = ET.fromstring(f"<body>{body_xml}</body>")
    return jats.Reader(Path("."), list(skip)).read(body, None)


def test_sections_paths_order_and_untitled_sec():
    sections = read("""
      <sec><title>A</title><p>a1</p>
        <sec><title>B</title><p>b1</p></sec>
        <sec><p>untitled</p></sec>
        <p>a2</p></sec>
      <sec><title>References</title><p>skip me</p></sec>
      <sec><title>Empty</title></sec>""")
    assert [(s.heading_path, [b.text for b in s.blocks]) for s in sections] == [
        (["A"], ["a1"]), (["A", "B"], ["b1"]), (["A"], ["untitled"]), (["A"], ["a2"])]


def test_paragraph_splits_nested_blocks_and_breaks():
    (s,) = read("""<sec><title>T</title><p>Before <xref>[1]</xref>
        <table-wrap><label>Table 1.</label><caption><p>Cap</p></caption>
          <table><tbody><tr><td>x<break/>y</td></tr></tbody></table></table-wrap>
        after.<inline-graphic/></p></sec>""")
    para, table, tail = s.blocks
    assert para == Paragraph("Before [1]") and tail == Paragraph("after.")
    assert table.caption == "Table 1. Cap" and table.rows == [["x y"]] and table.header == ["Column 1"]


def test_lists_ordered_and_nested():
    (s,) = read("""<sec><title>T</title><list list-type="order">
        <list-item><p>one</p><list><list-item><p>inner</p></list-item></list></list-item>
        <list-item><p>two</p></list-item></list></sec>""")
    assert s.blocks == [Paragraph("1. one\n  - inner\n2. two")]


def test_table_multilevel_header_spans_and_th_first_row():
    (s,) = read("""<sec><title>T</title><table-wrap><label>Table 2.</label><table>
        <thead><tr><th rowspan="2">Gene</th><th colspan="2">Frequency</th></tr>
               <tr><th>Prenatal</th><th>Postnatal</th></tr></thead>
        <tbody><tr><td rowspan="2">PTPN11</td><td>50%</td><td><list><list-item><p>a</p></list-item><list-item><p>b</p></list-item></list></td></tr>
               <tr><td>10%</td><td>20%</td></tr></tbody></table>
        <table-wrap-foot><fn><p>Note.</p></fn></table-wrap-foot></table-wrap>
        <table-wrap><table><tr><th>K</th><th>V</th></tr><tr><td>1</td><td>2</td></tr></table></table-wrap></sec>""")
    t1, t2 = s.blocks
    assert t1.header == ["Gene", "Frequency / Prenatal", "Frequency / Postnatal"]
    assert t1.rows == [["PTPN11", "50%", "a; b"], ["PTPN11", "10%", "20%"]] and t1.footnotes == "Note."
    assert (t2.header, t2.rows) == (["K", "V"], [["1", "2"]])


def test_boxed_text_title_and_figure_without_file():
    (s,) = read("""<sec><title>T</title><boxed-text><caption><title>Box</title></caption><p>inside</p></boxed-text>
        <fig><label>Figure 1.</label><caption><p>Fig</p></caption><graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="nope"/></fig></sec>""")
    assert s.blocks == [Paragraph("Box\ninside"), Figure("Figure 1. Fig", None)]


def test_uncited_floats_go_last():
    sections = read("""<sec><title>A</title><p>see <xref rid="f1">Figure 1</xref></p></sec>
        <sec sec-type="figs-and-tables"><fig id="f1"><label>Figure 1.</label></fig><fig id="f2"><label>Figure 2.</label></fig></sec>""")
    assert [(s.heading_path, s.blocks) for s in sections] == [
        (["A"], [Paragraph("see Figure 1"), Figure("Figure 1.", None)]), ([], [Figure("Figure 2.", None)])]
