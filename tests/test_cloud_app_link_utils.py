from app.executor.actions.link_utils import extract_cloud_app_key


def test_extracts_i_from_fragment_query():
    assert extract_cloud_app_key("https://host/path/#/?i=KWcMvfaFlhw=") == "KWcMvfaFlhw="


def test_extracts_i_with_other_parameters_and_url_encoding():
    link = "https://host/path?source=test#/page?x=1&i=abc%2B123%3D&tail=2"
    assert extract_cloud_app_key(link) == "abc+123="


def test_falls_back_for_legacy_or_unstructured_values():
    assert extract_cloud_app_key("prefix?i=legacy-key&x=1") == "legacy-key"
    assert extract_cloud_app_key("raw-key") == "raw-key"
