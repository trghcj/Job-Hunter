"""Fakes for the web app tests: token verifier, accounts gateway, sign-in helper."""

import re

from jobhunter.accounts import Identity, SignIn
from jobhunter.settings import Settings
from jobhunter.web.app import UserInfo
from jobhunter.web.firebase_auth import TokenError

FIREBASE = dict(firebase_api_key="AIza-test", firebase_auth_domain="test.firebaseapp.com",
                firebase_project_id="job-hunter-test", firebase_app_id="1:2:web:3")


def app_settings(**kw) -> Settings:
    base = dict(session_secret="s" * 40, web_https_only=False, **FIREBASE)
    base.update(kw)
    return Settings(**base)


class FakeVerifier:
    """Token "good:<uid>" is valid for Identity(uid, <uid>@gmail.com)."""

    def verify(self, token: str) -> Identity:
        if not token.startswith("good:"):
            raise TokenError("Sign-in failed, please try again.")
        uid = token.split(":", 1)[1]
        return Identity(uid, f"{uid}@gmail.com", uid.title())


class FakeAccounts:
    def __init__(self, owner_uid: str = "owner"):
        self.owner_uid = owner_uid
        self.users: dict[int, UserInfo] = {}
        self.by_uid: dict[str, int] = {}
        self.outcome: str | None = None  # force "blocked" / "full"

    def sign_in(self, identity: Identity) -> SignIn:
        if self.outcome:
            return SignIn(self.outcome, None)
        if identity.uid not in self.by_uid:
            uid = 1 if identity.uid == self.owner_uid else len(self.users) + 2
            self.by_uid[identity.uid] = uid
            self.users[uid] = UserInfo(uid, identity.email, identity.name, uid == 1, False)
        user = self.users[self.by_uid[identity.uid]]
        return SignIn("blocked", None) if user.blocked else SignIn("ok", user.id)

    def get_user(self, user_id: int) -> UserInfo | None:
        return self.users.get(user_id)

    def block(self, user_id: int) -> None:
        u = self.users[user_id]
        self.users[user_id] = UserInfo(u.id, u.email, u.name, u.is_owner, True)


def csrf_from(html: str) -> str:
    return re.search(r'(?:name="csrf" value|data-csrf)="([^"]+)"', html).group(1)


def sign_in(client, uid: str = "owner"):
    """Signs the test client in; returns the response of /auth/session."""
    token = csrf_from(client.get("/login").text)
    return client.post("/auth/session", json={"idToken": f"good:{uid}", "csrf": token})


def page_csrf(client, path: str = "/settings") -> str:
    return csrf_from(client.get(path).text)
