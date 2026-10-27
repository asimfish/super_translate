"""Real merged APS/PRL blocks from issue #7; no copyrighted PDF fixture."""
from types import SimpleNamespace

import pytest

from pdf_zh_translator import pdf_layout as m

BODY_PROSE = (
    '[81]. In general, more complicated knots require '
    'longerrange couplings in the tight-binding Hamiltonian.'
    ' Experimental realization and probe of knots.—The '
    'various proposed knots and their associated NH '
    'Hamiltonians can be realized in platforms such as '
    'photonic lattices or electric circuits [112]. For the '
    'former, the asymmetric coupling between the sites (ring'
    ' resonators) can be implemented via auxiliary microring'
    ' cavities, see Ref. [81] for details. For the latter, '
    'the NH Hamiltonians can be simulated by the admittance '
    'matrix. For example, the twister Hamiltonian\ue000H\ue001\ue000_{12}\ue001 '
    'is simulated by the periodic circuit shown in Fig. '
    '2(g). It consists of resistors R, inductors L, and '
    'negative impedance converters\ue000C\ue001\ue000_{i}\ue001 that provide '
    'intra- and interunit cell couplings, for details see '
    'Ref. [81]. Measurement of the admittance spectrum '
    '[60,62,113] yields f\ue000E\ue001\ue000_{j}\ue001ðkÞg, which provides a '
    'direct probe'
)

REFERENCE_LIST = (
    '\ue000^{*}\ue001ezhao2@gmu.edu [1] C. M. Bender, Making sense of '
    'non-Hermitian Hamiltonians, Rep. Prog. Phys. 70, 947 '
    '(2007). [2] I. Rotter, A non-Hermitian Hamilton '
    'operator and the physics of open quantum systems, J. '
    'Phys. A 42, 153001 (2009). [3] V. M. Martinez Alvarez, '
    'J. E. Barrios Vargas, M. Berdakin, and L. E. F. Foa '
    'Torres, Topological states of non- Hermitian systems, '
    'Eur. Phys. J. Special Topics 227, 1295 (2018). [4] R. '
    'El-Ganainy, K. G. Makris, M. Khajavikhan, Z. H. '
    'Musslimani, S. Rotter, and D. N. Christodoulides, Non- '
    'Hermitian physics and PT symmetry, Nat. Phys. 14, 11 '
    '(2018). [5] E. J. Bergholtz, J. C. Budich, and F. K. '
    'Kunst, Exceptional topology of non-Hermitian systems, '
    'arXiv:1912.10048. [6] N. Moiseyev, Non-Hermitian '
    'Quantum Mechanics (Cambridge University Press, New '
    'York, 2011). [7] A. Ghatak and T. Das, New topological '
    'invariants in non- Hermitian systems, J. Phys. Condens.'
    ' Matter 31, 263001 (2019). [8] L. E. F. Foa Torres, '
    'Perspective on topological states of non-Hermitian '
    'systems, J. Phys. Mater. 3, 014002 (2019). [9] Y. '
    'Ashida, Z. Gong, and Masahito Ueda, Non-Hermitian '
    'physics, arXiv:2006.01837.'
)

@pytest.mark.parametrize("text,want", [(BODY_PROSE, False), (REFERENCE_LIST, True)])
def test_headingless_prl_classifies_body_and_merged_references(text, want):
    m._bibliography_seen.clear()
    m._bibliography_ended = False
    m._bibliography_heading_size = 0.0
    block = SimpleNamespace(text=text, page_index=3, font_size=8.2, bold=False)
    try:
        assert m._looks_like_reference_entry_text(text) is want
        assert m._is_bibliography_context(block, []) is want
    finally:
        m._bibliography_seen.clear()
        m._bibliography_ended = False
        m._bibliography_heading_size = 0.0

@pytest.mark.parametrize("text", [
    "[81]. In general, we evaluate the model using the datasets in [2] and [3].",
    "[1] We compare the model with [2] and [3] in the following experiments.",
    "The method in [1] improves on [2]. We analyze [3] in the next section.",
])
def test_citation_count_alone_does_not_make_body_a_reference_list(text):
    assert not m._looks_like_reference_entry_text(text)


@pytest.mark.parametrize("prefix", [
    "A citation title continued from the previous column, J. Example 5, 19 (2015). ",
    "A journal article split across pages, Example Science 11, 82 (2018). ",
    "041103(R) (2017). ",
])
def test_merged_reference_list_can_start_with_previous_entry_tail(prefix):
    text = (
        prefix + "[17] B. Example, A research title, J. Example 6, 17 (2016). "
        "[18] C. Author, Another research title, Example Science 12, 88 (2017)."
    )
    assert m._looks_like_reference_entry_text(text)


@pytest.mark.parametrize("text", [
    "We cite [1] A. Example (2020) explains how the model improves accuracy. "
    "[2] B. Author (2021) studies the same scientific problem in depth.",
    "In this paper, we compare prior methods. "
    "[1] A. Example, Method one (2020). [2] B. Author, Method two (2021).",
])
def test_sequential_citations_and_years_do_not_protect_body_prose(text):
    assert not m._looks_like_numbered_reference_list(text)
    assert not m._looks_like_reference_entry_text(text)
