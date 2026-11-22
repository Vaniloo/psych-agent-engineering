"""Isolated read/controller probes for tests and offline benchmarks, without business writes."""

from langgraph.graph import END, START, StateGraph

from psych_agent_runtime.graph import AgentState


async def run_read_loop(runtime, state):
    graph = StateGraph(AgentState)
    for name, node in [
        ("initialize", runtime._loop_initialize),
        ("decide", runtime._loop_decide),
        ("tool", runtime._loop_tool),
        ("observe", runtime._loop_observe),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "decide")
    graph.add_conditional_edges(
        "decide", runtime._after_loop_decision, {"tool": "tool", "finish": END}
    )
    graph.add_edge("tool", "observe")
    graph.add_edge("observe", "decide")
    return await graph.compile().ainvoke(state)


async def run_controller(controller, plan, message, invoke, emit):
    loop = controller.start_loop(plan, message)

    async def call(name, _arguments):
        return await invoke(name)

    while True:
        loop = await controller.decide(loop, emit)
        if loop["next"]["tool"] in {"finish", "clarify"}:
            return loop["results"], loop["trace"]
        loop = controller.observe(await controller.execute(loop, call), emit)
