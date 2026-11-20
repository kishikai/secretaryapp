import sqlite3

import pytest

from app.db import MEMBERS_SCHEMA, connect, init_db
from tests.conftest import client_with_role


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "members.db")


@pytest.fixture
def secretary(db_path):
    return client_with_role(db_path, "secretary")


def csv_file(text: str):
    return {"file": ("members.csv", text.encode("utf-8"), "text/csv")}


def preview(client, text):
    return client.post("/members/import/preview", files=csv_file(text))


def member_count(db_path):
    with connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]


# ------------------------------------------------------------------- CRUD


def test_member_crud_search_and_groups(secretary):
    ana = secretary.post("/members", json={"full_name": "  Ana Reyes ", "email": "ana@example.com", "phone": ""})
    assert ana.status_code == 201
    ana = ana.json()
    assert ana["full_name"] == "Ana Reyes" and ana["phone"] is None and ana["active"] is True
    assert ana["group_ids"] == []
    ben = secretary.post("/members", json={"full_name": "Ben Cruz", "member_code": "B-1", "phone": "0917 555 1234"}).json()

    choir = secretary.post("/groups", json={"name": "Choir", "description": "Sunday"}).json()
    assert secretary.post("/groups", json={"name": "choir"}).status_code == 409  # case-insensitive unique
    assert secretary.put(f"/members/{ana['id']}/groups", json={"group_ids": [choir["id"]]}).json()["group_ids"] == [choir["id"]]
    assert secretary.put(f"/members/{ana['id']}/groups", json={"group_ids": [999]}).status_code == 422
    assert secretary.get("/groups").json()[0]["member_count"] == 1

    assert [m["full_name"] for m in secretary.get("/members").json()] == ["Ana Reyes", "Ben Cruz"]
    assert [m["id"] for m in secretary.get("/members", params={"q": "EXAMPLE"}).json()] == [ana["id"]]
    assert [m["id"] for m in secretary.get("/members", params={"q": "b-1"}).json()] == [ben["id"]]
    assert [m["id"] for m in secretary.get("/members", params={"q": "555"}).json()] == [ben["id"]]
    assert [m["id"] for m in secretary.get("/members", params={"group_id": choir["id"]}).json()] == [ana["id"]]

    changed = secretary.patch(f"/members/{ana['id']}", json={"notes": "Alto", "active": False}).json()
    assert changed["notes"] == "Alto" and changed["active"] is False and changed["email"] == "ana@example.com"
    assert [m["id"] for m in secretary.get("/members", params={"active": "false"}).json()] == [ana["id"]]
    assert [m["id"] for m in secretary.get("/members", params={"active": "true"}).json()] == [ben["id"]]
    assert secretary.get(f"/members/{ana['id']}").json()["notes"] == "Alto"
    assert secretary.get("/members/999").status_code == 404
    assert secretary.patch("/members/999", json={"notes": "x"}).status_code == 404

    assert secretary.patch(f"/members/{ben['id']}", json={"member_code": None}).json()["member_code"] is None
    secretary.patch(f"/members/{ana['id']}", json={"member_code": "A-1"})
    assert secretary.patch(f"/members/{ben['id']}", json={"member_code": "A-1"}).status_code == 409

    assert secretary.patch(f"/groups/{choir['id']}", json={"name": "Singers"}).json()["name"] == "Singers"
    assert secretary.delete(f"/groups/{choir['id']}").status_code == 204
    assert secretary.get(f"/members/{ana['id']}").json()["group_ids"] == []
    assert secretary.delete(f"/groups/{choir['id']}").status_code == 404
    assert secretary.delete(f"/members/{ana['id']}").status_code in (404, 405)  # no hard delete


@pytest.mark.parametrize(
    "body",
    [
        {"full_name": "   "},
        {},
        {"full_name": "x" * 201},
        {"full_name": "A", "email": "nobody"},
        {"full_name": "A", "email": "@example.com"},
        {"full_name": "A", "email": "a@b@c"},
        {"full_name": "A", "joined_on": "31/03/2024"},
        {"full_name": "A", "left_on": "2024-02-30"},
        {"full_name": "A", "phone": "1" * 41},
        {"full_name": "A", "surprise": 1},
    ],
)
def test_member_validation(secretary, body):
    assert secretary.post("/members", json=body).status_code == 422


# ------------------------------------------------------------------ roles


def test_role_matrix(db_path):
    secretary = client_with_role(db_path, "secretary")
    assistant = client_with_role(db_path, "assistant")
    viewer = client_with_role(db_path, "viewer")
    member = secretary.post("/members", json={"full_name": "Ana"}).json()
    group = secretary.post("/groups", json={"name": "Choir"}).json()

    assert viewer.get("/members").status_code == 200
    assert viewer.get(f"/members/{member['id']}").status_code == 200
    assert viewer.get("/groups").status_code == 200
    assert viewer.post("/members", json={"full_name": "B"}).status_code == 403
    assert viewer.patch(f"/members/{member['id']}", json={"notes": "x"}).status_code == 403
    assert viewer.put(f"/members/{member['id']}/groups", json={"group_ids": []}).status_code == 403
    assert viewer.post("/groups", json={"name": "Other"}).status_code == 403

    assert assistant.post("/members", json={"full_name": "B"}).status_code == 201
    assert assistant.post("/members", json={"full_name": "C", "active": False}).status_code == 403
    assert assistant.patch(f"/members/{member['id']}", json={"notes": "x"}).status_code == 200
    assert assistant.patch(f"/members/{member['id']}", json={"active": True}).status_code == 200  # unchanged
    assert assistant.patch(f"/members/{member['id']}", json={"active": False}).status_code == 403
    assert assistant.put(f"/members/{member['id']}/groups", json={"group_ids": [group["id"]]}).status_code == 200
    assert assistant.post("/groups", json={"name": "Other"}).status_code == 201
    assert assistant.delete(f"/groups/{group['id']}").status_code == 403
    assert assistant.post("/members/import/preview", files=csv_file("full_name\nA\n")).status_code == 403
    assert assistant.post("/members/import/confirm", json={"rows": []}).status_code == 403
    assert viewer.post("/members/import/preview", files=csv_file("full_name\nA\n")).status_code == 403


# ----------------------------------------------------------- import preview


def test_preview_writes_nothing_and_reports_row_errors(secretary, db_path):
    text = (
        "Full Name,Email,Phone,Joined On,Colour,Groups\n"
        "Ana Reyes,ana@example.com,,2024-01-05,red,Choir; Ushers;choir\n"
        ",x@example.com,,,,\n"
        "Ben,not-an-email,,05/01/2024,,\n"
        ",,,,,\n"
    )
    response = preview(secretary, text)
    assert response.status_code == 200
    body = response.json()
    assert body["columns"] == ["full_name", "email", "phone", "joined_on", "groups"]
    assert [row["row"] for row in body["rows"]] == [1, 2, 3]  # the blank 4th record is dropped
    first, second, third = body["rows"]
    assert first["errors"] == [] and first["data"]["groups"] == ["Choir", "Ushers"]
    assert first["data"]["phone"] is None
    assert any(e.startswith("full_name") for e in second["errors"])
    assert len(third["errors"]) == 2
    assert member_count(db_path) == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0


def test_preview_accepts_name_alias_and_bom(secretary):
    body = preview(secretary, "﻿Name,Notes\nAna,hi\n").json()
    assert body["columns"] == ["full_name", "notes"]
    assert body["rows"][0]["data"]["full_name"] == "Ana"


def test_preview_file_errors(secretary):
    assert preview(secretary, "email\na@b.com\n").status_code == 422
    assert preview(secretary, "").status_code == 422
    bad = secretary.post("/members/import/preview", files={"file": ("m.csv", "name\nJosé\n".encode("latin-1"), "text/csv")})
    assert bad.status_code == 422 and bad.json()["detail"] == "Save the file as CSV UTF-8"
    assert preview(secretary, "name\n" + "A\n" * 5001).status_code == 422
    assert preview(secretary, "name\n" + "A\n" * 5000).status_code == 200


def test_preview_finds_duplicates_of_existing_members(secretary):
    existing = secretary.post(
        "/members",
        json={"full_name": "Maria  Santos", "email": "Maria@Example.com", "phone": "+63 917 555 1234", "member_code": "M-7"},
    ).json()
    text = (
        "name,email,phone,member_code\n"
        "Someone,,,M-7\n"
        "Other,maria@example.COM,,\n"
        "Third,,(0917) 555-1234,\n"
        "Fourth,,12345,\n"          # too short to count as a phone match
        "maria SANTOS,,,\n"
    )
    rows = preview(secretary, text).json()["rows"]
    reasons = [[d["reason"] for d in row["duplicates"]] for row in rows]
    # Row 3: +63 917... and (0917)... have different digits, so that is not a phone match.
    # Row 4: too short to count as a phone.
    assert reasons == [["same member code"], ["same email"], [], [], ["same name"]]
    assert rows[0]["duplicates"][0]["member_id"] == existing["id"]

    local = secretary.post("/members", json={"full_name": "Pedro", "phone": "0917-555-9999"}).json()
    dup = preview(secretary, "name,phone\nQ,09175559999\n").json()["rows"][0]["duplicates"]
    assert dup == [{"member_id": local["id"], "full_name": "Pedro", "reason": "same phone"}]


def test_preview_finds_duplicates_within_the_file(secretary):
    text = (
        "name,email,phone,member_code\n"
        "Ana,ana@example.com,0917 555 0000,A1\n"
        "Bea,BEA@example.com,,\n"
        "Cara,ana@example.com,,\n"
        "Dina,,09175550000,\n"
        "Eve,,,A1\n"
        "ana,,,\n"
    )
    rows = preview(secretary, text).json()["rows"]
    assert rows[0]["duplicates"] == []
    assert rows[1]["duplicates"] == []
    assert rows[2]["duplicates"] == [{"member_id": None, "full_name": "Ana", "reason": "same email as row 1"}]
    assert rows[3]["duplicates"][0]["reason"] == "same phone as row 1"
    assert rows[4]["duplicates"][0]["reason"] == "same member code as row 1"
    assert rows[5]["duplicates"][0]["reason"] == "same name as row 1"


# ----------------------------------------------------------- import confirm


def row(action, member_id=None, **data):
    return {"action": action, "member_id": member_id, "data": {"full_name": "Someone", **data}}


def test_confirm_creates_updates_skips(secretary, db_path):
    existing = secretary.post(
        "/members", json={"full_name": "Maria", "email": "m@example.com", "notes": "keep me"}
    ).json()
    old_group = secretary.post("/groups", json={"name": "Choir"}).json()
    response = secretary.post(
        "/members/import/confirm",
        json={
            "rows": [
                row("create", full_name="Ana", email="ana@example.com", groups=["choir", "Ushers"]),
                row("update", existing["id"], full_name="Maria Santos", email=None, notes="", phone="0917 555 1234", groups=["Ushers"]),
                row("skip", full_name="Skipped", email="bad"),  # invalid but skipped, so ignored
            ]
        },
    )
    assert response.json() == {"created": 1, "updated": 1, "skipped": 1}
    members = {m["full_name"]: m for m in secretary.get("/members").json()}
    assert set(members) == {"Ana", "Maria Santos"}
    maria = members["Maria Santos"]
    assert maria["email"] == "m@example.com" and maria["notes"] == "keep me"  # never blanked
    assert maria["phone"] == "0917 555 1234"
    groups = {g["name"]: g for g in secretary.get("/groups").json()}
    assert set(groups) == {"Choir", "Ushers"}  # "choir" matched the existing group
    assert groups["Choir"]["id"] == old_group["id"]
    assert members["Ana"]["group_ids"] == sorted([groups["Choir"]["id"], groups["Ushers"]["id"]])
    assert maria["group_ids"] == [groups["Ushers"]["id"]]


@pytest.mark.parametrize(
    "bad",
    [
        row("create", email="nope"),
        row("create", full_name="  "),
        row("create", joined_on="yesterday"),
        row("update"),                 # no member_id
        row("update", 999),            # unknown member
        row("create", surprise="x"),
    ],
)
def test_confirm_with_one_bad_row_writes_nothing(secretary, db_path, bad):
    response = secretary.post(
        "/members/import/confirm",
        json={"rows": [row("create", full_name="Good", groups=["New"]), bad]},
    )
    assert response.status_code == 422
    assert member_count(db_path) == 0
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0


def test_confirm_unique_conflict_is_409_and_rolls_back(secretary, db_path):
    secretary.post("/members", json={"full_name": "Taken", "member_code": "X1"})
    response = secretary.post(
        "/members/import/confirm",
        json={"rows": [row("create", full_name="New"), row("create", full_name="Clash", member_code="X1")]},
    )
    assert response.status_code == 409 and "member code" in response.json()["detail"]
    assert member_count(db_path) == 1


def test_reimport_with_skipped_duplicates_creates_nothing(secretary, db_path):
    text = "name,email,groups\nAna,ana@example.com,Choir\nBen,ben@example.com,Choir\n"
    first = preview(secretary, text).json()
    assert all(r["duplicates"] == [] and r["errors"] == [] for r in first["rows"])
    confirm = lambda rows: secretary.post("/members/import/confirm", json={"rows": rows})
    assert confirm([{"action": "create", "data": r["data"]} for r in first["rows"]]).json()["created"] == 2

    second = preview(secretary, text).json()
    assert all(len(r["duplicates"]) == 1 for r in second["rows"])
    assert all(r["duplicates"][0]["member_id"] is not None for r in second["rows"])
    result = confirm([{"action": "skip", "data": r["data"]} for r in second["rows"]]).json()
    assert result == {"created": 0, "updated": 0, "skipped": 2}
    assert member_count(db_path) == 2
    assert secretary.get("/groups").json()[0]["member_count"] == 2


# --------------------------------------------------------------- migration


def test_init_db_adds_member_tables_to_an_older_database(db_path):
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:  # back to a database from before members existed
        conn.executescript("DROP TABLE member_groups; DROP TABLE groups; DROP TABLE members;")
    init_db(db_path)
    init_db(db_path)  # idempotent
    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"members", "groups", "member_groups"} <= tables
        conn.execute("INSERT INTO members (full_name) VALUES ('Ana')")
        conn.executescript(MEMBERS_SCHEMA)  # re-running keeps the data
        assert conn.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO members (full_name, active) VALUES ('B', 2)")


def test_add_and_remove_existing_members_on_a_group(tmp_path):
    from tests.conftest import client_with_role
    db = str(tmp_path / "group-members.db")
    secretary = client_with_role(db, "secretary")
    ann = secretary.post("/members", json={"full_name": "Ann"}).json()["id"]
    bob = secretary.post("/members", json={"full_name": "Bob"}).json()["id"]
    group = secretary.post("/groups", json={"name": "Choir"}).json()["id"]

    result = secretary.post(f"/groups/{group}/members", json={"member_ids": [ann, bob, ann]}).json()
    assert result["added"] == 2 and result["group"]["member_count"] == 2
    again = secretary.post(f"/groups/{group}/members", json={"member_ids": [ann]}).json()
    assert again["added"] == 0  # already in the group: left alone
    assert [m["full_name"] for m in secretary.get("/members", params={"group_id": group}).json()] == ["Ann", "Bob"]

    assert secretary.delete(f"/groups/{group}/members/{bob}").status_code == 204
    assert secretary.delete(f"/groups/{group}/members/{bob}").status_code == 404
    assert secretary.get(f"/members/{ann}").json()["group_ids"] == [group]

    assert secretary.post(f"/groups/{group}/members", json={"member_ids": [999]}).status_code == 422
    assert secretary.post(f"/groups/{group}/members", json={"member_ids": []}).status_code == 422
    assert secretary.post("/groups/999/members", json={"member_ids": [ann]}).status_code == 404

    assistant, viewer = client_with_role(db, "assistant"), client_with_role(db, "viewer")
    assert assistant.post(f"/groups/{group}/members", json={"member_ids": [bob]}).status_code == 200
    assert viewer.post(f"/groups/{group}/members", json={"member_ids": [bob]}).status_code == 403
    assert viewer.delete(f"/groups/{group}/members/{bob}").status_code == 403
