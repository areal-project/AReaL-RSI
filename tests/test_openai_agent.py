from areal_rsi.agent.openai_agent import OpenAIRSIAgent


class FakeOpenAIClient:
    def complete_sync(self, prompt: str) -> str:
        return """
        {
          "reply": "Ready to run.",
          "actions": [
            {
              "type": "start_loop",
              "args": {"initial_ckpt": "ckpt_000", "task_set": "swe_verified"},
              "requires_confirmation": true,
              "metadata": {}
            }
          ],
          "requires_confirmation": true,
          "metadata": {}
        }
        """


class DummyMemory:
    def get_context(self, run_id=None):
        return {"events": [], "insights": ""}


def test_openai_rsi_agent_parses_structured_action():
    agent = OpenAIRSIAgent(client=FakeOpenAIClient())

    response = agent.chat(
        "improve ckpt_000",
        controller=object(),
        memory=DummyMemory(),
        workflow_spec={},
    )

    assert response.reply == "Ready to run."
    assert response.requires_confirmation is True
    assert response.actions[0].type == "start_loop"
    assert response.actions[0].args["initial_ckpt"] == "ckpt_000"
