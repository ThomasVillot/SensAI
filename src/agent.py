import json
from .config import active_config
from .model_funcs import request_tool_model
from .output_guard import build_output_guard
from .injection_patterns import detect_prompt_injection, neutralize_prompt_injection
from .utils import extract_json_in_content, is_schema_valid, get_tool_schema, parse_tool_arguments, normalize_tool_calls
from .tools.handler import tool_handler
from mcp import Client, StdioServerParameters
import sys
from pathlib import Path


ACCEPT_TIMEOUT = 2

HISTORY: list[dict | None] = []

server: StdioServerParameters | None = None
mcp_client = None
mcp_client_context = None

async def connect_mcp():
    global server, mcp_client, mcp_client_context

    if mcp_client is not None:
        return mcp_client

    if server is None:
            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "tools.mcp_server", "--cache", active_config().cache_mode],
                cwd=Path(__file__).resolve().parent
            )

    mcp_client_context = Client(server)
    mcp_client = await mcp_client_context.__aenter__()

    return mcp_client

async def close_mcp():
    global mcp_client, mcp_client_context

    if mcp_client_context is not None:
        context = mcp_client_context

        mcp_client = None
        mcp_client_context = None

        await context.__aexit__(None, None, None)

async def execute_tool(tool_name: str, tool_args: dict):
    mcp_client = await connect_mcp()

    tools = await mcp_client.list_tools()

    if not any(tool.name == tool_name for tool in tools.tools):
        return { "error": f"tool name invalide: '{tool_name}'" }

    schema_valid, message = is_schema_valid(tool_args, get_tool_schema(active_config().tools, tool_name) or {})

    if not schema_valid:
        return message

    try:
        raw_result = await mcp_client.call_tool(tool_name, parse_tool_arguments(tool_args))

        content = getattr(raw_result, "content", [])
        texts = []
        for item in content:
            text = getattr(item, "text", None)
            if text is None:
                continue
            if detect_prompt_injection(text):
                text = neutralize_prompt_injection(text)
            texts.append(text)

        if getattr(raw_result, "is_error", False) or getattr(raw_result, "isError", False):
            return {"error": " ".join(texts) or f"l'outil '{tool_name}' a renvoyé une erreur"}

        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except json.JSONDecodeError:
                return texts[0]

        if texts:
            return texts

        return {"result": str(raw_result)}
    except Exception as exc:
        return {"error": f"l'outil '{tool_name}' a échoué : {type(exc).__name__}: {exc}"}

def action_key(tool_name: str, tool_args) -> tuple[str, str]:
    return tool_name, json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)

def forget_turn(user_message: str) -> None:
    for index in range(len(HISTORY) - 1, -1, -1):
        if HISTORY[index].get("role") == "user" and HISTORY[index].get("content") == user_message:
            del HISTORY[index:]
            return

async def run_agent(tool_model, user_message, current_timeout = 0, history = None):


    config = active_config()

    if history is None:
        if not HISTORY:
            HISTORY.append({
                "role": "system",
                "content": config.tool_system_prompt
            })

        messages = HISTORY

        if current_timeout == 0:
            HISTORY.append({
                "role": "user",
                "content": user_message
            })
    else:
        first_new_message = len(history.messages)
        messages = history.messages_with(user_message)

    guard = build_output_guard(config, messages)

    iterations = 0
    last_action, repeated = None, 0

    while True:

        if iterations >= config.max_iterations:
            yield {
                "type": "error",
                "message": (
                    f"Plafond de {config.max_iterations} itérations atteint : le modèle enchaîne "
                    "les appels d'outils sans conclure. Reformule ta demande, ou augmente "
                    "'max_iterations' dans config/config.json."
                )
            }
            return

        iterations += 1
        yield {"type": "step", "n": iterations, "max": config.max_iterations}

        final_response = None
        error_message = None
        if guard is not None:
            guard.reset()

        for event in request_tool_model(
            model=tool_model,
            messages=messages,
            tools=config.tools
        ):
            if event.get("type", "") == "token":
                if guard is None:
                    yield {"type": "token", "content": event.get("content", "")}
                    continue

                released, violation = guard.feed(event.get("content", ""))
                if violation:
                    if history is None:
                        forget_turn(user_message)
                    yield violation.event()
                    return
                if released:
                    yield {"type": "token", "content": released}

            elif event.get("type", "") == "done":
                final_response = event.get("message", "")

            elif event.get("type", "") == "error":
                if current_timeout < ACCEPT_TIMEOUT:
                    print(f"[timeout]: current_timeout: {current_timeout} | Cause: {event.get('message')}")
                    async for item in run_agent(tool_model, user_message, current_timeout + 1, history):
                        yield item
                    return
                else:
                    error_message = event.get("message", "")
                    if history is None:
                        HISTORY.append({"role": "tool", "content": error_message})
                    break


        if final_response is None:
            yield {
                "type": "error",
                "message": error_message or "réponse du modèle interrompue avant la fin"
            }
            return


        if guard is not None:
            released, violation = guard.finish(final_response.get("content"))
            if violation:
                if history is None:
                    forget_turn(user_message)
                yield violation.event()
                return
            if released:
                yield {"type": "token", "content": released}

        messages.append(final_response)

        tool_calls = final_response.get("tool_calls", [])

        if not tool_calls:
            tool_calls = normalize_tool_calls(
                extract_json_in_content(final_response.get("content", "")),
                tool_handler,
            )
            if tool_calls is None:
                break

        for call in tool_calls:

            tool_name = call.get("function", {}).get("name", "")
            tool_args = call.get("function", {}).get("arguments", {})

            action = action_key(tool_name, tool_args)
            repeated = repeated + 1 if action == last_action else 1
            last_action = action
            if repeated >= config.max_repeated_actions:
                yield {
                    "type": "error",
                    "message": (
                        f"Le modèle redemande {repeated} fois de suite le même appel ({tool_name}, arguments "
                        f"{action[1]}) sans progresser : tour arrêté. Reformule ta demande, ou règle "
                        "'max_repeated_actions' dans config/config.json."
                    )
                }
                return

            yield {"type": "tool_call", "name": tool_name, "arguments": tool_args}

            result = await execute_tool(tool_name, tool_args)

            yield {"type": "tool_result", "name": tool_name, "result": result}

            messages.append({
                "role": "tool",
                "tool_name": tool_name,
                "content": json.dumps(result, default=str)
            })

    if history is not None:
        history.add_messages(messages[first_new_message:], model=tool_model)

    yield {"type": "done_tool", "content": last_assistant_content(messages)}


def last_assistant_content(messages):
    return next(
        (m.get("content", "") for m in reversed(messages)
        if m.get("role") == "assistant"),
        ""
    )
