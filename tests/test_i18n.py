"""Language is presentation: switching it must not change project data or contracts."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from jinja2 import nodes

from app.core.i18n import ENGLISH
from app.services import requirement_digest_service as digest

ROOT = Path(__file__).resolve().parents[1]


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        for key, value in attrs:
            if key in {"title", "placeholder", "aria-label", "data-record-label"}:
                self.parts.append(value or "")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden -= 1

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


PAGES = ["/ui/login", "/ui/users", "/ui/account", "/ui/config", "/ui/projects",
         "/ui/projects/1", "/ui/features/1", "/ui/design-cases/1", "/ui/tasks/1",
         "/ui/source-requirements/1"] + [f"/ui/projects/1/{page}" for page in (
             "source-requirements", "constitution", "glossary", "change-requests",
             "design-cases", "requirements", "nfr-coverage", "integration", "system",
             "features", "tasks")]


@pytest.mark.parametrize("path", PAGES)
def test_english_page_has_no_untranslated_interface_text(client, path):
    response = client.get(path + "?lang=en")
    assert response.status_code == 200
    assert response.headers["Content-Language"] == "en"
    parser = VisibleText()
    parser.feed(response.get_data(as_text=True))
    visible = " ".join(parser.parts).replace("中文", "")
    assert not re.search(r"[\u4e00-\u9fff]", visible), visible


def test_language_precedence_and_persistence(client):
    assert 'lang="en"' in client.get('/ui/projects', headers={'Accept-Language': 'en-US,en;q=0.9'}).text
    response = client.get('/ui/projects?lang=zh', headers={'Accept-Language': 'en'})
    assert 'lang="zh"' in response.text
    assert 'HttpOnly' in response.headers['Set-Cookie']
    assert 'SameSite=Lax' in response.headers['Set-Cookie']
    assert 'lang="zh"' in client.get('/ui/projects', headers={'Accept-Language': 'en'}).text
    assert 'lang="en"' in client.get('/ui/projects?lang=en').text
    assert 'lang="en"' in client.get('/ui/projects?lang=../../etc/passwd').text
    assert 'lang="en"' in client.get('/ui/projects').text
    client.delete_cookie('harnoq_locale')
    for headers in ({}, {'Accept-Language': 'fr'}, {'Accept-Language': 'zh-CN,zh;q=0.9'}):
        response = client.get('/ui/projects', headers=headers)
        assert 'lang="en"' in response.text
        assert response.headers['Content-Language'] == 'en'
    assert 'lang="en"' in client.get('/ui/projects?lang=invalid').text


def test_switch_preserves_query_and_escapes_values(client):
    body = client.get('/ui/projects?search=%22%3E%3Cscript%3E&status=active&lang=en').text
    assert 'status=active' in body and 'lang=zh' in body
    assert '<script>' not in body
    assert 'id="harnoq-i18n"' in body
    assert body.index("i18n.js") < body.index("app.js")


def test_project_data_and_business_numbers_are_language_independent(client):
    project = client.post('/projects?lang=en', json={'name': '项目名称', 'description': '保存中文原文 {0} <b>文档</b>'}).get_json()
    en = client.get(f"/projects/{project['id']}?lang=en").get_json()
    zh = client.get(f"/projects/{project['id']}?lang=zh").get_json()
    assert en == zh
    assert en['name'] == '项目名称'
    assert en['description'] == '保存中文原文 {0} <b>文档</b>'
    assert en['project_number'].startswith('P')


def test_template_messages_and_placeholders_are_in_catalog(app):
    for path in (ROOT / 'app/templates').glob('*.html'):
        tree = app.jinja_env.parse(path.read_text())
        for call in tree.find_all(nodes.Call):
            if isinstance(call.node, nodes.Name) and call.node.name == '_' and isinstance(call.args[0], nodes.Const):
                assert call.args[0].value in ENGLISH, (path, call.args[0].value)
    for key, value in ENGLISH.items():
        assert value.strip(), key
        assert sorted(re.findall(r'\{\d+\}', key)) == sorted(re.findall(r'\{\d+\}', value)), key
        assert not re.search(r'[\u4e00-\u9fff]', value.replace('中文', '')), key


def test_english_requirement_markers_preserve_internal_categories():
    material = '''| Material | Source |
| --- | --- |
| Keep the original wording. | User-confirmed |
| Use an agreed default. | Agent-supplied (accepted) |
| Resolve the unknown. | Pending confirmation |
| Conflict check | No conflicts |
'''
    rows = digest.extract_lines(material)
    assert [row['mark'] for row in rows] == [digest.CONFIRMED, digest.SUPPLIED, digest.PENDING]
    assert rows[0]['text'] == 'Keep the original wording.'
    assert digest.extract_lines('- 中文原文。**用户确认**')[0]['mark'] == digest.CONFIRMED


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_javascript_language_runtime_and_delete_confirmation():
    result = subprocess.run(['node', str(ROOT / 'tests/js_i18n_smoke.cjs')], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_documentation_local_links():
    for path in [ROOT / 'README.md', *(ROOT / 'docs').rglob('*.md')]:
        source = path.read_text()
        # Exclude code examples; historical git-show paths intentionally stay historical.
        source = re.sub(r'```.*?```', '', source, flags=re.S)
        for target in re.findall(r'\]\(([^)]+)\)', source):
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            resolved = (path.parent / unquote(url.path)).resolve()
            assert resolved.exists(), f'{path.relative_to(ROOT)} -> {target}'


def test_language_policy_is_delivered_without_mutating_methods():
    from app.api.skills import _drop_map_rounds_once_mapped, _stage_methods
    methods = _stage_methods()
    for method in methods.values():
        assert "User-confirmed" in method["language_policy"]
        assert "identifiers and enum values" in method["language_policy"]
    original = methods["design"]
    trimmed = _drop_map_rounds_once_mapped(original, [{"feature_number": "P1-FEATURE-1"}])
    assert trimmed["language_policy"] == original["language_policy"]
    assert len(original["rounds"]) == 5
