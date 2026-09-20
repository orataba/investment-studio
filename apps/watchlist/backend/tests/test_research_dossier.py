from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from watchlist_app.api.routes.research_dossier import router
from watchlist_app.db.models import InstrumentDetail, InstrumentManualProfile
from watchlist_app.db.models.workbench import ResearchEntry, ResearchTopic
from watchlist_app.db.session import get_session_factory
from watchlist_app.services import research_dossier as service


@pytest.fixture
def dossier_client(client):
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with get_session_factory()() as session:
        for iid, kind in [("xlk", "etf"), ("xlf", "etf"), ("stock", "equity"), ("9988-hk", "equity"), ("518880-sh", "etf"),
                          ("index", "index"), ("public", "public_fund"), ("private", "private_fund")]:
            session.add(InstrumentDetail(instrument_id=iid, instrument_name=iid.upper(),
                instrument_type=kind, detail_view_type=kind, metadata_json={}))
        session.commit()
    with TestClient(app) as api:
        yield api


def url(iid="xlk"):
    return f"/api/research/instruments/{iid}/dossier"


def test_read_only_dossiers_select_methods_and_historical_cases_by_instrument(dossier_client):
    with get_session_factory()() as session:
        before = [session.scalar(select(func.count()).select_from(model)) for model in (ResearchEntry, ResearchTopic)]
    for iid in ("xlk", "xlf", "stock", "index", "public", "private"):
        response = dossier_client.get(url(iid))
        assert response.status_code == 200
        dossier = response.json()
        assert dossier["name"] == iid.upper()
        assert dossier["notebook"] is None and dossier["notebook_history"] == []
        assert dossier["materials"] == []
        assert dossier["mandate"]["instrument_id"] == iid
        assert dossier["mandate"]["entry_id"] is None and dossier["mandate"]["updated_at"] is None
        assert dossier["mandate"]["role"] == "research_method"
        assert dossier["mandate"]["focus"] and dossier["mandate"]["gaps"]
        assert all(item["role"] == "research_method" for item in dossier["frameworks"])
        sectors = [item["id"] for item in dossier["frameworks"] if item["id"].startswith("sector-")]
        assert sectors == ([f"sector-{iid}"] if iid in {"xlk", "xlf"} else [])
        assert len(dossier["historical_cases"]) == (14 if iid == "xlk" else 0)
        if iid == "xlk":
            case = dossier["historical_cases"][0]
            assert case["source_id"] == "historical:" + case["case_id"]
            assert case["role"] == "historical_research" and case["sources"]
            assert any("核证" in line or "核验" in line or "复核" in line for line in case["reuse_limitations"])
    with get_session_factory()() as session:
        assert before == [session.scalar(select(func.count()).select_from(model)) for model in (ResearchEntry, ResearchTopic)]
    assert dossier_client.get(url("not-registered")).status_code == 404


def _mandate_payload(mandate):
    return {key: mandate[key] for key in service.ResearchMandateInput.model_fields}


def test_mandates_use_actual_registration_and_asset_specific_methods(dossier_client):
    from investment_studio_instrument_core.db_models import Instrument, InstrumentReferenceSnapshot
    with get_session_factory()() as session:
        for iid, kind, currency, exchange, sections in [
            ("stock", "equity", "USD", "XNAS", {"profile": {"industry": "Semiconductors", "website": "https://issuer.example"}}),
            ("public", "public_fund", "CNY", None, {"fund_info": {"benchmark": "中证1000指数收益率×95%", "management": "登记管理人"}}),
        ]:
            session.add(Instrument(instrument_id=iid, instrument_name=iid, instrument_type=kind,
                currency=currency, exchange_code=exchange, quote_selection_policy_json={}))
            session.flush()
            session.add(InstrumentReferenceSnapshot(instrument_id=iid,
                value_json={"provider": "fixture", "fetched_at": "2026-09-01T10:00:00Z", "sections": sections}))
        session.commit()
    stock = dossier_client.get(url("stock")).json()["mandate"]
    public = dossier_client.get(url("public")).json()["mandate"]
    private = dossier_client.get(url("private")).json()["mandate"]
    assert stock["registration"]["currency"] == "USD"
    assert "Semiconductors" in stock["background"] and "Semiconductors" in stock["focus"][0]
    assert "https://issuer.example" in stock["source_plan"][0]
    assert "中证1000指数收益率×95%" in public["focus"][0]
    assert "登记管理人" in public["background"]
    assert any("不能由名称推断持仓" in item for item in private["focus"])
    assert private["registration"]["disclosed_strategy"] is None
    alibaba = dossier_client.get(url("9988-hk")).json()["mandate"]
    gold = dossier_client.get(url("518880-sh")).json()["mandate"]
    assert any("股数乘发行价" in item for item in alibaba["research_approach"])
    assert any("原已为负" in item for item in alibaba["mechanisms"])
    assert any("不能写成简单恒等关系" in item for item in gold["mechanisms"])
    assert any("实际利率" in item for item in gold["mechanisms"])
    assert "不是对当前" in alibaba["background"] and "不是对当前" in gold["background"]
    assert not any("EPS" in item for item in gold["mechanisms"])


def test_save_updates_one_owned_mandate_without_turning_it_into_material(dossier_client):
    material = dossier_client.post(url() + "/materials", json={"title": "原始材料", "body": "真实材料正文"}).json()
    initial = dossier_client.get(url()).json()["mandate"]
    payload = _mandate_payload(initial)
    payload.update(background="已登记资料：XLK。待验证假设：新投入是否改善现金回报。", focus=["检验实际资本回报"], gaps=["尚缺最新季度原文"])
    first = dossier_client.put(url() + "/mandate", json=payload)
    assert first.status_code == 200
    payload["focus"] = ["结合新原文重新核对投入回收"]
    second = dossier_client.put(url() + "/mandate", json=payload)
    assert second.status_code == 200
    assert first.json()["entry_id"] == second.json()["entry_id"] == "dossier-mandate:xlk"
    assert second.json()["updated_at"] >= first.json()["updated_at"]
    current = dossier_client.get(url()).json()
    assert current["mandate"]["user_focus"] == payload["focus"]
    assert current["mandate"]["focus"] == initial["focus"]
    assert [item["entry_id"] for item in current["materials"]] == [material["entry_id"]]
    assert "source_id" not in current["mandate"]
    assert dossier_client.get(url("xlf")).json()["mandate"]["entry_id"] is None
    with get_session_factory()() as session:
        entries = list(session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id == "dossier:xlk")))
        assert len(entries) == 2
        note = next(entry for entry in entries if entry.entry_id == "dossier-mandate:xlk")
        assert note.kind == "note" and note.context_json["role"] == "research_mandate"
    assert dossier_client.put(url() + "/mandate", json={**payload, "instrument_id": "xlf"}).status_code == 422


def test_initialization_and_review_updates_share_callers_transaction(dossier_client):
    with get_session_factory()() as session:
        first = service.ensure_mandate(session, "stock")
        assert first["entry_id"] == "dossier-mandate:stock"
        session.rollback()
    assert dossier_client.get(url("stock")).json()["mandate"]["entry_id"] is None
    with get_session_factory()() as session:
        first = service.ensure_mandate(session, "stock")
        session.commit()
        retained = service.ensure_mandate(session, "stock")
        # SQLite drops timezone metadata; ensure initialization did not change the timestamp.
        assert datetime.fromisoformat(retained["updated_at"]).replace(tzinfo=None) == datetime.fromisoformat(first["updated_at"]).replace(tzinfo=None)
        payload = service.ResearchMandateInput.model_validate({**_mandate_payload(first), "focus": ["本轮待复核更新"]})
        service.save_mandate(session, "stock", payload, commit=False, origin="research")
        session.rollback()
    assert dossier_client.get(url("stock")).json()["mandate"]["focus"] == first["focus"]


def test_mandate_rejects_cross_instrument_or_portfolio_topic_ownership(dossier_client):
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="dossier:xlk", title="错误归属", instrument_ids=["xlf"], portfolio_id="private"))
        session.flush()
        session.add(ResearchEntry(entry_id="dossier-mandate:xlk", topic_id="dossier:xlk", kind="note", title="不能串用",
            context_json={"role": "research_mandate", "instrument_id": "xlf", "mandate": {}}))
        session.commit()
    assert dossier_client.get(url()).status_code == 422
    payload = _mandate_payload(dossier_client.get(url("xlf")).json()["mandate"])
    assert dossier_client.put(url() + "/mandate", json=payload).status_code == 422


def test_materials_preserve_three_clocks_and_reuse_stable_topic(dossier_client):
    response = dossier_client.post(url() + "/materials", json={"title": "公司公告", "body": "收入与成本的原文。",
        "source": "https://issuer.example/report", "published_at": "2026-06-30", "effective_date": "2026-03-31"})
    assert response.status_code == 201
    material = response.json()
    assert material["source_id"] == "material:" + material["entry_id"]
    assert material["metadata"]["published_at"] == "2026-06-30"
    assert material["metadata"]["effective_date"] == "2026-03-31"
    assert material["recorded_at"] and material["body"] == "收入与成本的原文。"
    second = dossier_client.post(url() + "/materials", json={"title": "会议纪要", "body": "用户提供的证据",
        "source": "", "published_at": "2026-07-01T16:00:00+08:00"})
    assert second.status_code == 201
    assert second.json()["metadata"]["published_at"] == "2026-07-01T16:00:00+08:00"
    assert dossier_client.post(url() + "/materials", json={"title": " ", "body": " ", "source": ""}).status_code == 422
    with get_session_factory()() as session:
        topic = session.get(ResearchTopic, "dossier:xlk")
        assert topic.instrument_ids == ["xlk"] and topic.portfolio_id is None
        assert len(list(session.scalars(select(ResearchEntry).where(ResearchEntry.topic_id == topic.topic_id)))) == 2
    assert len(dossier_client.get(url()).json()["materials"]) == 2
    assert dossier_client.get(url("xlf")).json()["materials"] == []


def test_private_conversation_evidence_is_not_automatically_shared(dossier_client):
    with get_session_factory()() as session:
        for topic_id, ids, portfolio in [("single", ["xlk"], None), ("multi", ["xlk", "xlf"], None),
                                         ("portfolio", ["xlk"], "private-account"), ("other", ["xlf"], None)]:
            session.add(ResearchTopic(topic_id=topic_id, title=topic_id, instrument_ids=ids, portfolio_id=portfolio))
        session.flush()
        for topic_id in ("single", "multi", "portfolio", "other"):
            session.add(ResearchEntry(entry_id=topic_id, topic_id=topic_id, kind="evidence", title="file",
                body=topic_id, source="original file", context_json={"file_name": "source.pdf"}))
        session.add(ResearchEntry(entry_id="pm", topic_id="single", kind="decision", title="PM观点", body="我的判断"))
        session.add(ResearchEntry(entry_id="note", topic_id="single", kind="evidence", title="聊天记录", body="没有上传文件"))
        session.commit()
    materials = dossier_client.get(url()).json()["materials"]
    assert materials == []


def test_fund_directory_reads_real_owned_files_and_keeps_unread_records_explicit(dossier_client, tmp_path):
    from watchlist_app.api.routes.funds import _instrument_document_dir
    folder = _instrument_document_dir("private")
    folder.mkdir(parents=True)
    (folder / "manager.txt").write_text("实际管理人报告正文", encoding="utf-8")
    outside = tmp_path / "not-this-instrument.txt"
    outside.write_text("不得读入", encoding="utf-8")
    (folder / "outside.txt").symlink_to(outside)
    with get_session_factory()() as session:
        session.add(InstrumentManualProfile(instrument_id="private", updated_at=datetime.now(UTC),
            documents_payload_json={"current_documents": [
                {"title": "季报", "stored_file_name": "manager.txt", "as_of_date": "2026-06-30",
                 "uploaded_at": "2026-08-01T08:00:00+00:00", "download_url": "/api/instruments/private/documents/files/manager.txt"},
                {"title": "缺失文件", "stored_file_name": "gone.pdf"},
                {"title": "资料链接", "source": "https://manager.example/material"},
                {"title": "越界文件", "stored_file_name": "outside.txt"},
            ]}))
        session.commit()
    records = {item["title"]: item for item in dossier_client.get(url("private")).json()["materials"]}
    assert records["季报"]["body"] == "实际管理人报告正文"
    assert records["季报"]["entry_id"] is None
    assert records["季报"]["metadata"]["effective_date"] == "2026-06-30"
    assert records["季报"]["recorded_at"] == "2026-08-01T08:00:00+00:00"
    assert records["缺失文件"]["metadata"]["extraction_status"] == "missing"
    for title in ("缺失文件", "资料链接", "越界文件"):
        assert records[title]["body"] == "" and records[title]["metadata"]["extraction"]


def test_file_upload_reuses_original_download_and_extracts_material_body(dossier_client):
    response = dossier_client.post(url("public") + "/files", files={"file": ("facts.txt", "公告原文".encode(), "text/plain")},
        data={"title": "持有人说明", "source": "基金管理人", "published_at": "2026-08-02", "effective_date": "2026-06-30"})
    assert response.status_code == 201, response.text
    material = response.json()
    assert material["body"] == "公告原文" and material["title"] == "持有人说明"
    assert material["source"] == f"/api/research/entries/{material['entry_id']}/file"
    assert material["metadata"]["source"] == "基金管理人"
    assert material["metadata"]["published_at"] == "2026-08-02"
    assert dossier_client.get(url("public")).json()["materials"][0]["entry_id"] == material["entry_id"]


def test_notebook_uses_completed_instrument_research_and_preserves_original_sources(dossier_client):
    now = datetime.now(UTC)
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="instrument-events:xlk", title="研究追踪", instrument_ids=["xlk"]))
        session.flush()
        for index, status, scoped_id in [(0, "completed", "xlk"), (1, "completed", "xlk"),
                                          (2, "failed", "xlk"), (3, "completed", "xlf")]:
            session.add(ResearchEntry(entry_id=f"run-{index}", topic_id="instrument-events:xlk", kind="analysis",
                title="研究", body="不是资料", status=status, created_at=now + timedelta(days=index),
                context_json={"sector_run": True, "instrument_ids": [scoped_id], "cutoff": f"2026-09-0{index + 1}",
                    "reviews": {scoped_id: {"status": "limited", "research": {
                        "fundamental_view": f"基本面判断{index}", "questions": [{"key": "cash", "status": "open"}],
                        "important_changes": [f"变化{index}"], "sources": [{"source_id": f"original-{index}", "text": "原文"}]}}}}))
        session.commit()
    dossier = dossier_client.get(url() + "?include_history=true").json()
    assert dossier["notebook"]["run_id"] == "run-1"
    assert dossier["notebook"]["fundamental_view"] == "基本面判断1"
    assert dossier["notebook"]["sources"] == [{"source_id": "original-1"}]
    with get_session_factory()() as session:
        assert service.read_dossier(session, "xlk")["notebook"]["sources"] == [{"source_id": "original-1", "text": "原文"}]
    assert [item["run_id"] for item in dossier["notebook_history"]] == ["run-1", "run-0"]
    assert dossier["notebook_history"][1]["important_changes"] == ["变化0"]
    assert dossier_client.get(url()).json()["notebook_history"] == []


def test_mandate_history_records_changes_and_quiet_save_keeps_version_and_date(dossier_client):
    payload = _mandate_payload(dossier_client.get(url("stock")).json()["mandate"])
    first = dossier_client.put(url("stock") + "/mandate", json=payload).json()
    quiet = dossier_client.put(url("stock") + "/mandate", json=payload).json()
    assert quiet["version_id"] == first["version_id"]
    assert datetime.fromisoformat(quiet["updated_at"]).replace(tzinfo=None) == datetime.fromisoformat(first["updated_at"]).replace(tzinfo=None)
    revised = dossier_client.put(url("stock") + "/mandate", json={**payload, "focus": ["新增竞争假设"]}).json()
    assert revised["version_id"] != first["version_id"]
    assert revised["versions"][0]["version_id"] == first["version_id"]
    assert revised["versions"][0]["focus"] == first["focus"]
    with get_session_factory()() as session:
        old = service.read_dossier_version(session, "stock", first["version_id"])
    assert old["kind"] == "mandate" and old["value"]["focus"] == first["focus"]
    assert "registration" not in old["value"]


def test_mandate_keeps_user_focus_while_research_evolves_its_own_document(dossier_client):
    with get_session_factory()() as session:
        initial = service.ensure_mandate(session, "stock")
        assert initial["author"]["origin"] == "initial" and initial["user_focus"] == []
        payload = service.ResearchMandateInput.model_validate({**_mandate_payload(initial), "focus": ["用户要求检验资本开支回报"]})
        manual = service.save_mandate(session, "stock", payload)
        assert manual["author"]["origin"] == "user"
        assert manual["author"]["user_id"] == "pm-one" and initial["author"]["user_id"] is None
        assert manual["focus"] == initial["focus"] and manual["user_focus"] == payload.focus
        revised = service.save_mandate(session, "stock", service.ResearchMandateInput.model_validate({
            **payload.model_dump(), "background": "研究补充的待验证背景", "focus": ["研究员继续核对需求"]}), origin="research")
        assert revised["author"]["user_id"] is None and revised["author"]["display_name"] == "研究员"
        assert revised["focus"] == ["研究员继续核对需求"]
        assert revised["user_focus"] == manual["user_focus"] and revised["author"]["origin"] == "research"
        assert revised["versions"][-1]["author"] == manual["author"]
        assert revised["versions"][-1]["user_focus"] == manual["user_focus"]
        old = service.read_dossier_version(session, "stock", manual["version_id"])
        assert old["value"]["user_focus"] == manual["user_focus"]
        # An explicit human save can change the instruction even when document text is unchanged.
        accepted = service.save_mandate(session, "stock", service.ResearchMandateInput.model_validate(_mandate_payload(revised)))
        assert accepted["version_id"] != revised["version_id"]
        assert accepted["user_focus"] == revised["focus"] and accepted["author"]["origin"] == "user"
        cleared = service.save_mandate(session, "stock", service.ResearchMandateInput.model_validate({**_mandate_payload(accepted), "focus": []}))
        assert cleared["user_focus"] == [] and cleared["focus"] == revised["focus"]


def test_quiet_checks_keep_latest_clock_without_duplicating_notebook_versions(dossier_client):
    from watchlist_app.services.research_notebook import ResearchNotebook, retain_notebook
    first = retain_notebook(ResearchNotebook(next_research=["继续核对需求"]), None, {}, "first", "2026-09-01T00:00:00+00:00")
    quiet = retain_notebook(ResearchNotebook(), first, {}, "quiet", "2026-09-02T00:00:00+00:00")
    with get_session_factory()() as session:
        session.add(ResearchTopic(topic_id="instrument-events:stock", title="追踪", instrument_ids=["stock"]))
        session.flush()
        for index, (run_id, research) in enumerate([("first", first), ("quiet", quiet)]):
            session.add(ResearchEntry(entry_id=run_id, topic_id="instrument-events:stock", kind="analysis", status="completed", title="研究",
                completed_at=datetime.now(UTC) + timedelta(hours=index), context_json={"sector_run": True,
                    "instrument_ids": ["stock"], "cutoff": f"2026-09-0{index + 1}T00:00:00+00:00",
                    "reviews": {"stock": {"status": "completed", "research": research}}}))
        session.commit()
        current = service.read_dossier(session, "stock", include_history=True)
        assert current["notebook"]["run_id"] == "quiet"
        assert current["notebook"]["checked_at"] == "2026-09-02T00:00:00+00:00"
        assert len(current["notebook_history"]) == 1
        assert current["notebook_history"][0]["run_id"] == "first"
        assert current["notebook_history"][0]["version_id"] == first["version_id"]
        assert service.read_dossier_version(session, "stock", first["version_id"])["information_cutoff"] == "2026-09-01T00:00:00+00:00"


def test_published_chat_research_and_forecast_history_share_dossier_without_later_evidence(dossier_client):
    from watchlist_app.services.research_notebook import ResearchNotebook, retain_notebook, research_sources
    cutoff = "2026-09-07T00:00:00+00:00"
    original = {"source_id": "original:first", "source_type": "public_source", "text": "当时披露的原文", "published_at": "2026-09-06"}
    later = {**original, "source_id": "original:later", "text": "后来披露的结果", "published_at": "2026-09-08"}
    first = retain_notebook(ResearchNotebook(forecasts=[{"key": "demand", "claim": "需求可能改善", "horizon": "下次披露",
        "source_ids": [original["source_id"]]}]), None, {original["source_id"]: original}, "first", cutoff)
    forecast_version = first["forecasts"][0]["version_id"]
    second = retain_notebook(ResearchNotebook(forecast_reviews=[{"key": "demand-review", "forecast_key": "demand",
        "forecast_version_id": forecast_version, "outcome": "需求改善，价格上涨", "mechanism_assessment": "价格也可能受到其他因素推动",
        "source_ids": [later["source_id"]]}]), first, {original["source_id"]: original, later["source_id"]: later}, "chat", "2026-09-09T00:00:00+00:00")
    with get_session_factory()() as session:
        session.add_all([ResearchTopic(topic_id="instrument-events:stock", title="追踪", instrument_ids=["stock"]),
                         ResearchTopic(topic_id="shared-chat", title="用户讨论", instrument_ids=["stock"])])
        session.flush()
        for run_id, topic, status, marker, body, delta in [
            ("first", "instrument-events:stock", "completed", "sector_run", first, 0),
            ("chat", "shared-chat", "draft", "research_run", second, 1),
            ("unpublished", "shared-chat", "draft", "research_run", {"fundamental_view": "未发布回答"}, 2),
        ]:
            session.add(ResearchEntry(entry_id=run_id, topic_id=topic, kind="analysis", status=status, title="研究",
                completed_at=datetime.now(UTC) + timedelta(hours=delta), context_json={marker: True,
                    "instrument_ids": ["stock"], "cutoff": cutoff if delta == 0 else "2026-09-09T00:00:00+00:00",
                    "reviews": {"stock": {"status": "pending" if run_id == "unpublished" else "completed", "research": body}}}))
        session.commit()
        current = service.read_dossier(session, "stock", include_history=True)
        version = service.read_dossier_version(session, "stock", forecast_version)
        full_first = service.read_dossier_version(session, "stock", first["version_id"])
        assert current["notebook"]["run_id"] == "chat"
        assert current["notebook_history"][1]["notebook"]["forecasts"][0]["claim"] == "需求可能改善"
        assert version["value"]["claim"] == "需求可能改善"
        assert version["sources"] == [original]
        assert full_first["sources"] == [original]
        assert version["information_cutoff"] == cutoff
        cases = current["historical_cases"]
        assert len(cases) == 1 and cases[0]["source_type"] == "research_review"
        assert cases[0]["instrument_id"] == "stock" and cases[0]["forecast_version_id"] == forecast_version
        sources = research_sources({"cutoff": "2026-09-09T00:00:00+00:00", "research_dossiers": [current]}, "next")
        assert cases[0]["source_id"] not in sources
        with pytest.raises(LookupError):
            service.read_dossier_version(session, "xlf", forecast_version)


def test_historical_atlas_is_owned_by_each_instrument_not_a_default_xlk_template(dossier_client, tmp_path, monkeypatch):
    import json
    from shutil import copyfile
    (tmp_path / "historical_cases").mkdir()
    for name in ("frameworks.json", "mandate_seeds.json"):
        copyfile(service.DATA_ROOT / name, tmp_path / name)
    (tmp_path / "historical_cases" / "stock.json").write_text(json.dumps({
        "metadata": {"atlas_id": "stock-history"}, "reuse_limitations": ["背景不同不能直接类比"],
        "cases": [{"case_id": "stock-case", "source_id": "historical:stock-case", "case_title": "该公司的历史案例",
                   "role": "historical_research", "sources": [{"url": "https://issuer.example/history"}]}]}))
    monkeypatch.setattr(service, "DATA_ROOT", tmp_path)
    stock = dossier_client.get(url("stock")).json()
    assert stock["historical_cases"][0]["instrument_id"] == "stock"
    assert stock["historical_cases"][0]["atlas_id"] == "stock-history"
    assert dossier_client.get(url("xlf")).json()["historical_cases"] == []
