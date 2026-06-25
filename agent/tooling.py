"""Lightweight runtime helpers for PaperExpress research agents.

The project intentionally avoids new third-party dependencies, so this module
keeps tool dispatch and trajectory recording small and explicit.
"""

from datetime import datetime


class AgentTrajectory:
    """Append-only execution trace for agent planning, actions and reflection."""

    def __init__(self):
        self.steps = []

    def add(self, step_type, title, content="", tool="", input_data=None,
            output_summary="", status="success", metadata=None):
        entry = {
            "index": len(self.steps) + 1,
            "type": str(step_type or "step"),
            "title": str(title or ""),
            "content": str(content or ""),
            "tool": str(tool or ""),
            "input": input_data if input_data is not None else "",
            "outputSummary": str(output_summary or ""),
            "status": str(status or "success"),
            "metadata": metadata or {},
            "created": datetime.now().isoformat(timespec="seconds")
        }
        self.steps.append(entry)
        return entry

    def to_list(self):
        return list(self.steps)


class AgentTool:
    """Simple callable tool wrapper."""

    def __init__(self, name, description, runner):
        self.name = str(name or "").strip()
        self.description = str(description or "").strip()
        self.runner = runner
        if not self.name:
            raise ValueError("tool name is required")
        if not callable(self.runner):
            raise ValueError("tool runner must be callable")

    def run(self, parameters):
        return self.runner(parameters or {})


class ToolRegistry:
    """Registry used by agents to execute named tools."""

    def __init__(self):
        self._tools = {}

    def register(self, tool):
        self._tools[tool.name] = tool
        return tool

    def execute(self, name, parameters=None):
        if name not in self._tools:
            raise KeyError(f"未注册工具: {name}")
        return self._tools[name].run(parameters or {})

    def describe(self):
        return [
            {"name": tool.name, "description": tool.description}
            for tool in self._tools.values()
        ]
