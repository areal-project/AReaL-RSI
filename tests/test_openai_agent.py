from areal_rsi.agent.action_tools import ActionToolCall, rsi_action_tools
from areal_rsi.agent.openai_agent import OpenAIRSIAgent


class FakeOpenAIClient:
    def __init__(self):
        self.prompt = ""
        self.tools = []
        self.tool_choice = None

    def tool_calls_sync(self, prompt: str, *, tools, tool_choice="auto", max_tool_calls=1):
        self.prompt = prompt
        self.tools = tools
        self.tool_choice = tool_choice
        return [
            ActionToolCall(
                name="start_loop",
                arguments={"initial_ckpt": "ckpt_000", "task_set": "swe_verified"},
            )
        ]


class DummyMemory:
    def get_context(self, run_id=None):
        return {"events": [], "insights": ""}


def test_openai_rsi_agent_uses_tool_call_for_action():
    client = FakeOpenAIClient()
    agent = OpenAIRSIAgent(client=client)

    response = agent.chat(
        "improve ckpt_000",
        controller=object(),
        memory=DummyMemory(),
        workflow_spec={},
    )

    assert response.reply == "Prepared RSI loop for ckpt_000 on swe_verified."
    assert response.requires_confirmation is True
    assert response.actions[0].type == "start_loop"
    assert response.actions[0].args["initial_ckpt"] == "ckpt_000"
    assert response.actions[0].metadata == {"source": "openai_tool_call"}
    assert client.tool_choice == "auto"
    assert [tool["name"] for tool in client.tools] == [
        "start_loop",
        "get_status",
        "inspect_run",
        "resume_run",
        "stop_run",
    ]


def test_rsi_action_tools_define_start_loop_schema():
    start_loop = rsi_action_tools()[0]

    assert start_loop["name"] == "start_loop"
    assert start_loop["parameters"]["required"] == ["initial_ckpt", "task_set"]
    assert start_loop["parameters"]["additionalProperties"] is False
    assert start_loop["parameters"]["properties"]["method"]["enum"] == ["sft", "opd"]
    assert start_loop["parameters"]["properties"]["max_iterations"]["minimum"] == 1
    assert start_loop["parameters"]["properties"]["harness_config"]["additionalProperties"] is False
