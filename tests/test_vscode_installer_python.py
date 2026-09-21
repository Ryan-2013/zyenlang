from tools.install_vscode_extension import _registered_version


def test_registered_version_reads_exact_extension_id() -> None:
    output = "other.zyenlang@9.0.0\nzyenlang.zyenlang@0.3.2\n"
    assert _registered_version(output, "zyenlang.zyenlang") == "0.3.2"


def test_registered_version_is_case_insensitive() -> None:
    assert _registered_version("ZyenLang.ZyenLang@0.3.2\n", "zyenlang.zyenlang") == "0.3.2"


def test_registered_version_returns_none_when_missing() -> None:
    assert _registered_version("other.extension@1.0.0\n", "zyenlang.zyenlang") is None
