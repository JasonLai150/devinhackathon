"""Client for B's env contract.

  POST /reset?seed=N[&popup=0|1&labels=i]  -> {session_id, entry_url, seed}
  GET  /s/<sid>/...                        -> env pages + stub APIs
  GET  /state?session=<sid>                -> full DB dump + meta
  POST /verify {task_id, session_id}       -> {passed, reason}
  GET  /sessions                           -> session list

Two transports, one interface:

  EnvClient("http://localhost:5001")  -> real server (agent rollouts)
  EnvClient.for_app(create_app(...))  -> in-process via Flask test client
                                         (tests + sims, no server needed)

Anti-cheat hygiene: agents must browse only /s/<sid>/ paths. /state is for
the runner, verifiers, and metrics — never handed to the agent (B flagged
it is unguarded and would trivialize tasks).
"""
import json
import urllib.error
import urllib.parse
import urllib.request


class EnvError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class EnvClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self._test_client = None
        self._opener = urllib.request.build_opener(_NoRedirect)

    @classmethod
    def for_app(cls, app):
        """In-process client over a Flask app (env.app.create_app())."""
        c = cls("http://env.local")
        c._test_client = app.test_client()
        return c

    # ------------------------------------------------------------ transport

    def _request(self, method, path, params=None, json_body=None, form=None):
        """-> (status, body bytes). Never raises on HTTP errors."""
        if self._test_client is not None:
            kw = {"query_string": params or {}}
            if json_body is not None:
                kw["json"] = json_body
            if form is not None:
                kw["data"] = form
            r = self._test_client.open(path, method=method, **kw)
            return r.status_code, r.get_data()

        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data, headers = None, {}
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
        try:
            with self._opener.open(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError as e:
            raise EnvError(f"{method} {url}: {e.reason}") from e

    # -------------------------------------------------------------- contract

    def reset(self, seed=None, popup=None, labels=None):
        params = {k: v for k, v in
                  {"seed": seed, "popup": popup, "labels": labels}.items()
                  if v is not None}
        status, body = self._request("POST", "/reset", params=params)
        if status != 200:
            raise EnvError(f"reset failed: {status} {body[:200]!r}")
        return json.loads(body)

    def get(self, path, params=None):
        """GET an env page or stub API under /s/<sid>/. -> (status, text)."""
        status, body = self._request("GET", path, params=params)
        return status, body.decode(errors="replace")

    def get_json(self, path, params=None):
        status, body = self._request("GET", path, params=params)
        if status != 200:
            raise EnvError(f"GET {path} -> {status}")
        return json.loads(body)

    def post_form(self, path, form):
        """POST a urlencoded form (mutation endpoint). -> status."""
        status, _ = self._request("POST", path, form=form)
        return status

    def verify(self, task_id, session_id):
        _, body = self._request("POST", "/verify", json_body={
            "task_id": task_id, "session_id": session_id})
        return json.loads(body)

    def get_state(self, session_id):
        """Full session DB dump + meta. Runner/verifier tooling only."""
        return self.get_json("/state", params={"session": session_id})

    def list_sessions(self):
        return self.get_json("/sessions")["sessions"]
