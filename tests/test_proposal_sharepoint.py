"""SharePoint save + PDF for proposals (M9, D103), against a fake Microsoft Graph.

No network: an ``httpx.MockTransport`` plays Graph, and every request it sees is
kept so the tests can assert the things that matter in production - the upload
never overwrites a file we did not create, the pre-authorised upload URL gets no
bearer token, and a regenerate replaces the same item.
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import httpx
import pytest

from engine.proposal import sharepoint
from engine.proposal.model import Proposal, SharePointFile

CFG = sharepoint.SharePointConfig("tenant", "client", "secret")


class FakeGraph:
    def __init__(self, folders=None, pdf_failures=0):
        self.requests = []
        self.folders = folders if folders is not None else ["2821 - KS Moremi", "28210 - Someone Else", "2887 - Chubb"]
        self.prep_exists = False
        self.pdf_failures = pdf_failures
        self.sessions = 0
        self.uploaded = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urllib.parse.unquote(str(request.url))
        m = request.method
        if "login.microsoftonline.com" in url:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if m == "GET" and url.endswith("/sites/dynamicauctioneers.sharepoint.com:/sites/Marketing"):
            return httpx.Response(200, json={"id": "site1"})
        if m == "GET" and url.endswith("/sites/site1/drive"):
            return httpx.Response(200, json={"id": "drive1"})
        if m == "GET" and "/root:/3. PROPERTIES 2026/6. AAA Properties:/children" in url:
            value = [{"id": f"f{i}", "name": n, "folder": {}} for i, n in enumerate(self.folders)]
            value.append({"id": "file9", "name": "2821 notes.txt"})
            return httpx.Response(200, json={"value": value})
        if m == "GET" and url.endswith("/children?$select=id,name,folder,webUrl&$top=999") and "/items/" in url:
            value = [{"id": "prep1", "name": "Auction Prep", "folder": {}}] if self.prep_exists else []
            return httpx.Response(200, json={"value": value})
        if m == "POST" and url.endswith("/items/f0/children"):
            body = json.loads(request.content)
            assert body["name"] == "Auction Prep" and body["@microsoft.graph.conflictBehavior"] == "fail"
            self.prep_exists = True
            return httpx.Response(201, json={"id": "prep1", "name": "Auction Prep", "folder": {}})
        if m == "POST" and url.endswith(":/createUploadSession"):
            body = json.loads(request.content)
            assert body["item"]["@microsoft.graph.conflictBehavior"] == "rename"
            name = url.split("/items/prep1:/")[1].split(":/createUploadSession")[0]
            self.sessions += 1
            return httpx.Response(200, json={"uploadUrl": f"https://upload.example/{self.sessions}?name={urllib.parse.quote(name)}"})
        if m == "POST" and "/items/docx1/createUploadSession" in url:
            return httpx.Response(200, json={"uploadUrl": "https://upload.example/replace-docx?name=x.docx"})
        if m == "POST" and "/items/pdf1/createUploadSession" in url:
            return httpx.Response(200, json={"uploadUrl": "https://upload.example/replace-pdf?name=x.pdf"})
        if m == "PUT" and url.startswith("https://upload.example/"):
            assert "authorization" not in request.headers
            total = int(request.headers["Content-Range"].split("/")[1])
            end = int(request.headers["Content-Range"].split("-")[1].split("/")[0])
            if end + 1 < total:
                return httpx.Response(202, json={"nextExpectedRanges": [f"{end + 1}-"]})
            name = url.split("name=")[1]
            if name.endswith(".docx"):
                item = {"id": "docx1", "name": "2821 - Auction proposal 1.docx", "webUrl": "https://sp/docx"}
            else:
                item = {"id": "pdf1", "name": "2821 - Auction proposal 1.pdf", "webUrl": "https://sp/pdf"}
            return httpx.Response(201, json=item)
        if m == "GET" and url.endswith("/items/docx1/content?format=pdf"):
            if self.pdf_failures:
                self.pdf_failures -= 1
                return httpx.Response(503, json={"error": {"message": "busy"}})
            return httpx.Response(302, headers={"Location": "https://download.example/p.pdf"})
        if m == "GET" and url == "https://download.example/p.pdf":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"%PDF-1.7 fake")
        return httpx.Response(599, json={"error": {"message": f"unexpected {m} {url}"}})


def _proposal(tmp_path: Path, size=2048) -> Proposal:
    out = tmp_path / "out"
    out.mkdir()
    (out / "2821 - Auction proposal.docx").write_bytes(b"D" * size)
    return Proposal(dp="2821", docx_file="out/2821 - Auction proposal.docx")


def _publish(tmp_path, graph, proposal):
    client = httpx.Client(transport=httpx.MockTransport(graph))
    return sharepoint.publish(proposal, tmp_path, CFG, http=client, sleep=lambda s: None)


def test_config_needs_all_three_credentials():
    assert sharepoint.config_from_env({"MS_GRAPH_TENANT_ID": "t", "MS_GRAPH_CLIENT_ID": "c"}) is None
    cfg = sharepoint.config_from_env({
        "MS_GRAPH_TENANT_ID": "t", "MS_GRAPH_CLIENT_ID": "c", "MS_GRAPH_CLIENT_SECRET": "s",
        "PROPOSAL_SHAREPOINT_FOLDER": "/3. PROPERTIES 2027/6. AAA Properties/",
    })
    assert cfg.properties_folder == "3. PROPERTIES 2027/6. AAA Properties"


def test_publish_saves_both_files_beside_a_hand_made_one_and_keeps_the_ids(tmp_path):
    graph = FakeGraph()
    updated = _publish(tmp_path, graph, _proposal(tmp_path))

    assert updated.sharepoint_folder == "2821 - KS Moremi/Auction Prep"
    assert updated.sharepoint_docx == SharePointFile(item_id="docx1", name="2821 - Auction proposal 1.docx", web_url="https://sp/docx")
    assert updated.sharepoint_pdf.item_id == "pdf1"
    assert (tmp_path / updated.pdf_file).read_bytes() == b"%PDF-1.7 fake"
    # The PDF follows the Word file's final (renamed) name.
    assert any("2821 - Auction proposal 1.pdf" in urllib.parse.unquote(str(r.url)) for r in graph.requests)


def test_regenerating_replaces_the_same_items(tmp_path):
    graph = FakeGraph()
    graph.prep_exists = True
    proposal = _proposal(tmp_path)
    proposal.sharepoint_docx = SharePointFile(item_id="docx1", name="2821 - Auction proposal 1.docx")
    proposal.sharepoint_pdf = SharePointFile(item_id="pdf1", name="2821 - Auction proposal 1.pdf")
    _publish(tmp_path, graph, proposal)
    urls = [urllib.parse.unquote(str(r.url)) for r in graph.requests]
    assert graph.sessions == 0  # no new "rename" uploads
    assert any("/items/docx1/createUploadSession" in u for u in urls)
    assert any("/items/pdf1/createUploadSession" in u for u in urls)


def test_a_large_file_goes_up_in_chunks(tmp_path):
    graph = FakeGraph()
    _publish(tmp_path, graph, _proposal(tmp_path, size=6 * 1024 * 1024))
    ranges = [r.headers["Content-Range"] for r in graph.requests if r.method == "PUT" and ".docx" in str(r.url)]
    assert ranges == [f"bytes 0-5242879/{6 * 1024 * 1024}", f"bytes 5242880-6291455/{6 * 1024 * 1024}"]


def test_a_busy_conversion_is_retried(tmp_path):
    graph = FakeGraph(pdf_failures=2)
    updated = _publish(tmp_path, graph, _proposal(tmp_path))
    assert updated.pdf_file


def test_no_property_folder_is_a_plain_message(tmp_path):
    with pytest.raises(sharepoint.SharePointError, match="no folder for 2821"):
        _publish(tmp_path, FakeGraph(folders=["2887 - Chubb"]), _proposal(tmp_path))


def test_two_matching_folders_are_refused(tmp_path):
    with pytest.raises(sharepoint.SharePointError, match="More than one folder"):
        _publish(tmp_path, FakeGraph(folders=["2821 - KS Moremi", "2821 - Duplicate"]), _proposal(tmp_path))


def test_nothing_is_uploaded_before_the_word_file_exists(tmp_path):
    with pytest.raises(sharepoint.SharePointError, match="Generate the Word file first"):
        _publish(tmp_path, FakeGraph(), Proposal(dp="2821"))
