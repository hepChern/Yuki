from Yuki.kernel import file_types


def test_is_plot_by_extension():
    assert file_types.is_plot("mass.png") is True
    assert file_types.is_plot("fit.PDF") is True          # case-insensitive
    assert file_types.is_plot("ntuple.root") is False


def test_classify():
    assert file_types.classify("mass.png") == "plot"
    assert file_types.classify("ntuple.root") == "data"


def test_make_predicate_type_keywords():
    plots = file_types.make_predicate("plots")
    data = file_types.make_predicate("data")
    every = file_types.make_predicate("all")
    assert plots("mass.png") and not plots("ntuple.root")
    assert data("ntuple.root") and not data("mass.png")
    assert every("anything.xyz") is True


def test_make_predicate_glob_and_name():
    glob = file_types.make_predicate("*.root")
    name = file_types.make_predicate("mass.png")
    assert glob("ntuple.root") and not glob("mass.png")
    assert name("mass.png") and not name("other.png")
