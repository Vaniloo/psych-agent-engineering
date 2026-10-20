import pytest
from pydantic import ValidationError

from psych_agent_runtime.knowledge_sources import SOURCES, licensed_source, source_policy
from psych_agent_runtime.practical_corpus import extract
from psych_agent_runtime.rag import RagEngine
from psych_agent_runtime.schemas import KnowledgeUpsertRequest


@pytest.mark.parametrize("host", list(SOURCES))
def test_source_license_requires_real_publisher_path_and_policy(host):
    policy = SOURCES[host]
    url = "https://" + host + policy["paths"][0] + "test"
    metadata = {"source": url, "license": policy["license"], "licenseSource": policy["policy"],
                "publisher": policy["publisher"]}
    assert licensed_source(metadata)
    assert not licensed_source({**metadata, "license": "public-domain-other"})
    assert not licensed_source({**metadata, "publisher": "Unknown"})
    assert not licensed_source({**metadata, "licenseSource": "https://example.com/policy"})
    assert not licensed_source({**metadata, "source": url.replace(host, host + ".evil.test")})


@pytest.mark.parametrize("url", [
    "http://www.nhlbi.nih.gov/health/sleep", "https://www.nhlbi.nih.gov:9000/health/sleep",
    "https://user:pass@www.nhlbi.nih.gov/health/sleep", "https://www.nhlbi.nih.gov/news/test",
    "https://www.nhlbi.nih.gov/health/../admin", "https://www.nhlbi.nih.gov/health/%2e%2e/admin",
    "https://www.cdc.gov/mmwr/test", "https://www.cdc.gov/mental-health/test?track=yes",
    "https://www.cdc.gov/mental-health/test#part", "https://127.0.0.1/health/test",
])
def test_unreviewed_or_ambiguous_source_urls_rejected(url):
    assert source_policy(url) is None


def test_original_nimh_snapshot_is_compatible():
    assert licensed_source({"source": "https://www.nimh.nih.gov/health/topics/anxiety-disorders",
                            "license": "public-domain-nimh"})
    assert not licensed_source({"source": "https://www.nhlbi.nih.gov/health/insomnia",
                                "license": "public-domain-nhlbi"})


def test_nhlbi_extraction_drops_menu_and_language_switcher_without_losing_body():
    html = '''<h1>Healthy sleep habits</h1><article class="node node--type-health-topics">
      <div class="block-nhlbi-global-breadcrumb"><li>Home</li><li>Diagnosis</li></div>
      <div class="block-language"><h2>Language switcher</h2><li>Español</li></div>
      <p>Use the hour before bed for quiet time. Avoid bright artificial light.
      The effects of caffeine can last up to 8 hours. Keep your room cool, quiet, and dark.</p>
      <h2>Strategies for shift workers</h2><p>If you work shifts, limit caffeine use to
      the first part of your shift. Remove sound and light distractions in your bedroom.</p>
      </article>'''
    title, sections, _ = extract(html, "https://www.nhlbi.nih.gov/health/sleep-deprivation/healthy-sleep-habits")
    assert title == "Healthy sleep habits" and len(sections) == 2
    assert "8 hours" in sections[0]["text"]
    assert "switcher" not in str(sections) and "Español" not in str(sections)
    assert "Diagnosis" not in str(sections)


def test_inline_style_does_not_pollute_title_or_disable_all_sections():
    html = '''<main><h1><style>.weight{color:red!important}</style>Meditation</h1>
      <h2><style>.weight{color:red!important}</style>Are these practices safe?</h2>
      <p>Do not use meditation to replace conventional care or to postpone seeing a
      health care provider. Some people report negative experiences such as increased anxiety.</p>
      <h2>References</h2><p>Bibliography bibliography bibliography bibliography bibliography bibliography.</p></main>'''
    title, sections, _ = extract(html, "https://www.nccih.nih.gov/health/meditation", "weight")
    assert title == "Meditation" and len(sections) == 1
    assert "Do not" in sections[0]["text"] and "increased anxiety" in sections[0]["text"]
    assert "important" not in str(sections) and "Bibliography" not in str(sections)


def test_excluded_parent_section_blocks_child_content_and_hotline_is_not_indexed():
    html = '''<main><h1>Relaxation</h1><h2>Medicines</h2><h3>Details</h3>
      <p>This excluded subsection has drug instructions and must not become public self-care advice.</p>
      <h2>More To Consider</h2><p>Do not replace conventional care with relaxation techniques.
      If you have severe or long-lasting symptoms, see your health care provider.</p>
      <p>Call 988 for support; this local number must not be copied into general knowledge.</p>
      <h2>Reprints</h2><p>Copyright boilerplate repeated many times does not belong in retrieval context.</p></main>'''
    _, sections, _ = extract(html, "https://www.nccih.nih.gov/health/relaxation")
    assert len(sections) == 1
    assert "Do not replace" in sections[0]["text"]
    assert all(term not in str(sections) for term in ["drug instructions", "988", "boilerplate"])


def test_unknown_layout_quarantines_instead_of_indexing_navigation():
    with pytest.raises(ValueError, match="layout changed"):
        extract("<h1>Sleep</h1><main><p>Only a navigation page.</p></main>",
                "https://www.nhlbi.nih.gov/health/sleep")


def test_expanded_metadata_remains_bounded_and_cannot_override_runtime_fields():
    p = SOURCES["www.nccih.nih.gov"]
    metadata = {"publisher": p["publisher"], "licenseSource": p["policy"], "audience": "一般公众",
                "contentKind": "public-health-education", "license": p["license"]}
    request = KnowledgeUpsertRequest(document_id="corpus-test", content="Public text", metadata=metadata)
    assert request.metadata["audience"] == "一般公众"
    with pytest.raises(ValidationError):
        KnowledgeUpsertRequest(document_id="test", content="text", metadata={**metadata, "source": "spoof"})


def test_new_licensed_sources_can_participate_in_topic_scope():
    p = SOURCES["www.nhlbi.nih.gov"]
    corpus = {"ids": ["sleep", "spoof"], "documents": ["trusted text", "untrusted text"],
        "metadatas": [{"source": "https://www.nhlbi.nih.gov/health/sleep-deprivation/healthy-sleep-habits",
          "document_id": "sleep", "keywords": "睡眠 咖啡因", "license": p["license"],
          "licenseSource": p["policy"], "publisher": p["publisher"]},
          {"source": "https://example.com/health/sleep", "document_id": "spoof", "keywords": "咖啡因",
           "license": p["license"]}]}
    assert RagEngine._licensed_topic_corpus("咖啡因对睡眠有什么影响？", corpus)["ids"] == ["sleep"]
