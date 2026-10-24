"""Paths + config for the loop workstream.

Defaults point at B's dev fixture (env/) and the shared contract stubs
(fixtures/) so everything works today; env vars override once A's real
artifacts land.
"""
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOOP = os.path.dirname(os.path.abspath(__file__))


def env_url():
    return os.environ.get("LOOP_ENV_URL", "http://localhost:5001")


def tasks_path():
    return os.environ.get("LOOP_TASKS", os.path.join(_REPO, "env", "tasks.json"))


def spec_path():
    return os.environ.get(
        "LOOP_SPEC", os.path.join(_REPO, "env", "spec", "env_spec.json"))


def trajectory_path():
    return os.environ.get(
        "LOOP_TRAJECTORY", os.path.join(_REPO, "fixtures", "trajectory.json"))


def artifacts_dir():
    return os.environ.get("LOOP_ARTIFACTS", os.path.join(_LOOP, "artifacts"))


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_tasks(path=None, spec=None):
    """env/tasks.json -> {task_id: task}. Same shape as verifiers.load_tasks."""
    if path is None and spec and "tasks" in spec:
        return {t["id"]: t for t in spec["tasks"]}
    return {t["id"]: t for t in load_json(path or tasks_path())["tasks"]}


def load_spec(path=None):
    return load_json(path or spec_path())


def artifact_path(name):
    os.makedirs(artifacts_dir(), exist_ok=True)
    return os.path.join(artifacts_dir(), name)
