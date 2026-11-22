from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app import auth
from app.db import connect

router = APIRouter(prefix="/auth")


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


@router.post("/login", dependencies=[Depends(auth.check_csrf_header)])
def login(credentials: LoginIn, request: Request, response: Response):
    limiter = request.app.state.login_limiter
    ip = request.client.host if request.client else "unknown"
    wait = limiter.retry_after(ip)
    if wait:
        raise HTTPException(
            429, "Too many failed sign-in attempts; try again later", headers={"Retry-After": str(wait)}
        )
    with connect(request.app.state.db_path) as conn:
        admin = auth.authenticate(conn, credentials.username, credentials.password)
        if admin is None:
            limiter.record_failure(ip)
            raise HTTPException(401, "Wrong username or password")
        token = auth.create_session(conn, admin["id"])
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_MAX_AGE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=auth.cookie_secure(),
    )
    return {"username": admin["username"], "role": admin["role"]}


@router.post("/logout", status_code=204, dependencies=[Depends(auth.check_csrf_header)])
def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        with connect(request.app.state.db_path) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (auth.token_digest(token),))
    response = Response(status_code=204)
    response.delete_cookie(
        auth.SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="strict",
        secure=auth.cookie_secure(),
    )
    return response


@router.get("/me")
def me(user=Depends(auth.require_user)):
    return {"username": user["username"], "role": user["role"]}
