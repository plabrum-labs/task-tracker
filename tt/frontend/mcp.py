"""The domain with an MCP client on the end of it.

A third frontend beside ``cli`` and ``tui``, over the same ``api``: an agent
lists projects and issues and runs the same actions a person runs, through
the same availability checks, with no privileged path. Nothing here names an
action key — a write tool exists because ``action_schemas`` or ``top_level_offers``
lists it, and its input schema is the ``Field``\\ s the payload derived. Adding a
sixth action changes no line of this file.

Fully local, single-user, one SQLite file, so the deployment is settled: stdio
transport, no auth, no sessions. This is the first frontend meant to run beside
another — an agent driving it while the TUI holds the same ``tt.db`` open — which
``db.connect``'s WAL and busy timeout are what make safe.

Sync core, async shell. ``build_tools`` and ``dispatch`` are plain functions of
the registry and the engine, tested directly against the in-memory database with
no event loop; the two SDK handlers are thin adapters that unwrap the request
params and wrap the sync results in the SDK result types. So no asyncio test
plugin is needed — the async edge is never what a test drives.

MCP has one flat tool namespace, but ``editTitle``/``editStatus``/``delete`` live
on both domains, so every generated name is ``{domain}_{KEY}`` — a uniform rule
that names no key by hand. ``project_createProject`` reads redundantly; it is that
same rule applied to the one top-level action, left uniform on purpose.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from sqlalchemy import Engine

from tt import schema
from tt.domains.issue import api as issue_api
from tt.domains.project import api as project_api
from tt.platform.actions import (
    REFUSALS,
    ActionResponse,
    Enum,
    Field,
    Invalid,
    Offer,
    OptionalText,
    Refused,
    Runnable,
    Text,
)

# What a write tool hands its address and payload to — ``project_action`` or
# ``issue_action`` with the arguments reordered to (engine, address, key, payload).
type Writer = Callable[[Engine, Any, str, dict[str, Any]], ActionResponse]

# What a tool does with the open engine and the arguments a client sent: a read
# renders JSON, a write runs the action and reports its message. Both return the
# text a success carries; a refusal is raised and mapped to an error result.
type Handler = Callable[[Engine, dict[str, Any]], str]


# --- the offers an agent reads before it picks an action ------------------


def _field_json(field: Field) -> dict[str, Any]:
    """One argument of an offered action, as the JSON an agent reads before it
    fills anything in — the same projection the CLI's ``show`` hands out."""
    base: dict[str, Any] = {
        "name": field.name,
        "required": field.required,
        "description": field.description,
    }
    match field.kind:
        case Text():
            return {**base, "type": "text"}
        case OptionalText():
            return {**base, "type": "text", "optional": True}
        case Enum(values=values):
            return {**base, "type": "enum", "values": values}


def _offer_json(offer: Offer) -> dict[str, Any]:
    """One offered action, as the JSON an agent reads before it picks anything."""
    fields: dict[str, Any] = {"key": offer.key, "label": offer.label}
    match offer.state:
        case Runnable():
            fields["state"] = "runnable"
        case Refused(reason):
            fields["state"] = "refused"
            fields["reason"] = reason
    fields["arguments"] = [_field_json(field) for field in offer.fields]
    return fields


def _dump(value: Any) -> str:
    return json.dumps(value, indent=2)


# --- the writes -----------------------------------------------------------
#
# The ``api`` opens the transaction, so one call is one write. A project write
# carries an optional slug — an object action passes one, the top-level
# ``createProject`` passes ``None``.


def _project_write(engine: Engine, slug: Any, key: str, payload: dict[str, Any]) -> ActionResponse:
    return project_api.project_action(engine, key, payload, slug)


def _issue_write(
    engine: Engine, issue_id: Any, key: str, payload: dict[str, Any]
) -> ActionResponse:
    return issue_api.issue_action(engine, key, payload, issue_id)


# --- the reads ------------------------------------------------------------


def _read_project_list(engine: Engine, _arguments: dict[str, Any]) -> str:
    return _dump([item.model_dump(mode="json") for item in project_api.project_list(engine)])


def _read_project_show(engine: Engine, arguments: dict[str, Any]) -> str:
    detail, offers = project_api.project_detail(engine, arguments["slug"])
    actions = [_offer_json(offer) for offer in offers]
    return _dump({"project": detail.model_dump(mode="json"), "actions": actions})


def _read_issue_list(engine: Engine, arguments: dict[str, Any]) -> str:
    project = arguments.get("project")
    if project is not None:
        if project_api.project_get(engine, project) is None:
            raise Invalid(f"no project {project!r}")
        slugs = [project]
    else:
        slugs = [item.slug for item in project_api.project_list(engine)]
    rows = [issue for slug in slugs for issue in issue_api.issue_list(engine, slug)]
    return _dump([row.model_dump(mode="json") for row in rows])


def _read_issue_show(engine: Engine, arguments: dict[str, Any]) -> str:
    detail, offers = issue_api.issue_detail(engine, arguments["id"])
    actions = [_offer_json(offer) for offer in offers]
    return _dump({"issue": detail.model_dump(mode="json"), "actions": actions})


# --- the tool surface -----------------------------------------------------


@dataclass(frozen=True)
class _Address:
    """The object a write addresses, as one required top-level property beside the
    payload fields — the MCP analogue of the CLI's address argument."""

    name: str
    schema: dict[str, Any]


_SLUG = _Address("slug", {"type": "string", "description": "The project's slug."})
_ID = _Address("id", {"type": "integer", "description": "The issue's id."})


@dataclass(frozen=True)
class _Tool:
    """One tool, built once and read two ways: ``build_tools`` renders the SDK
    ``Tool`` a client sees, ``dispatch`` runs the ``Handler`` behind it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    run: Handler


def _field_schema(field: Field) -> dict[str, Any]:
    """One payload field as a JSON-Schema property. Every enum is already the
    lowercase-name string choices ``fields_of`` normalised it to — the exact wire
    form the decoder accepts, and what the CLI advertises too."""
    match field.kind:
        case Text() | OptionalText():
            prop: dict[str, Any] = {"type": "string"}
        case Enum(values=values):
            prop = {"type": "string", "enum": values}
    if field.description is not None:
        prop["description"] = field.description
    return prop


def _input_schema(fields: list[Field], address: _Address | None) -> dict[str, Any]:
    """The tool's input schema: the address (if any) as a required property, then a
    property per payload field, flat — mirroring the CLI's address-plus-options."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    if address is not None:
        properties[address.name] = address.schema
        required.append(address.name)
    for field in fields:
        properties[field.name] = _field_schema(field)
        if field.required:
            required.append(field.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _write_handler(writer: Writer, key: str, address: _Address | None) -> Handler:
    """Split the address off the arguments and route the rest as the payload. A
    top-level create has no address, so its slug is ``None``."""

    def run(engine: Engine, arguments: dict[str, Any]) -> str:
        payload = dict(arguments)
        value = payload.pop(address.name) if address is not None else None
        return writer(engine, value, key, payload).message

    return run


def _write_tools(
    domain: str,
    writer: Writer,
    address: _Address,
    schemas: list[tuple[str, list[Field]]],
    top_level: list[Offer],
) -> list[_Tool]:
    """A tool per object action, addressed, then a tool per top-level action, not.
    The name is ``{domain}_{KEY}`` throughout; the description is generated from the
    key, so nothing here is hand-written per action."""
    tools = [
        _Tool(
            name=f"{domain}_{key}",
            description=f"Run the {key} action on a {domain} addressed by {address.name}.",
            input_schema=_input_schema(fields, address),
            run=_write_handler(writer, key, address),
        )
        for key, fields in schemas
    ]
    tools += [
        _Tool(
            name=f"{domain}_{offer.key}",
            description=f"Run the {offer.key} action.",
            input_schema=_input_schema(offer.fields, None),
            run=_write_handler(writer, offer.key, None),
        )
        for offer in top_level
    ]
    return tools


def _read_tools() -> list[_Tool]:
    """The four fixed reads. Unlike the writes these are not generated — there is a
    fixed set of ways to read — so their descriptions and schemas are spelled out."""
    return [
        _Tool(
            name="project_list",
            description="List every live project.",
            input_schema={"type": "object", "properties": {}},
            run=_read_project_list,
        ),
        _Tool(
            name="project_show",
            description="Show one project and the actions it offers.",
            input_schema=_input_schema([], _SLUG),
            run=_read_project_show,
        ),
        _Tool(
            name="issue_list",
            description="List live issues, across every project or within one.",
            input_schema={
                "type": "object",
                "properties": {
                    "project": {"type": "string", "description": "Only this project's issues."}
                },
            },
            run=_read_issue_list,
        ),
        _Tool(
            name="issue_show",
            description="Show one issue and the actions it offers.",
            input_schema=_input_schema([], _ID),
            run=_read_issue_show,
        ),
    ]


def _tools() -> list[_Tool]:
    """Every tool the server exposes: the fixed reads, then a generated write per
    registered action. The one source ``build_tools`` and ``dispatch`` share, so the
    advertised list and the runnable map cannot drift."""
    return [
        *_read_tools(),
        *_write_tools(
            "project",
            _project_write,
            _SLUG,
            project_api.action_schemas(),
            project_api.top_level_offers(),
        ),
        *_write_tools("issue", _issue_write, _ID, issue_api.action_schemas(), []),
    ]


def build_tools() -> list[types.Tool]:
    """The SDK tool list a client sees, one per ``_Tool``."""
    return [
        types.Tool(name=tool.name, description=tool.description, input_schema=tool.input_schema)
        for tool in _tools()
    ]


# --- dispatch and error mapping -------------------------------------------


def _content(message: str) -> list[types.ContentBlock]:
    return [types.TextContent(type="text", text=message)]


def dispatch(engine: Engine, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    """Run the named tool and map its outcome to a result. A refusal — the object's
    answer, the MCP analogue of the CLI's exit 123 — comes back as an error result,
    a normal tool outcome and not a crash. A ``SQLAlchemyError`` is deliberately not
    caught: the machine failing propagates, the same split the CLI already draws."""
    tool = _by_name().get(name)
    if tool is None:
        return types.CallToolResult(content=_content(f"no tool {name!r}"), is_error=True)
    try:
        message = tool.run(engine, arguments)
    except REFUSALS as refusal:
        return types.CallToolResult(content=_content(str(refusal)), is_error=True)
    return types.CallToolResult(content=_content(message))


def _by_name() -> dict[str, _Tool]:
    return {tool.name: tool for tool in _tools()}


# --- the async shell ------------------------------------------------------


async def _serve(engine: Engine) -> None:
    """The stdio server. The two handlers adapt the SDK's request params to the sync
    ``build_tools``/``dispatch`` and wrap their results back up."""

    async def on_list_tools(
        _ctx: object, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=build_tools())

    async def on_call_tool(
        _ctx: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return dispatch(engine, params.name, params.arguments or {})

    server = Server(name="tt", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def serve(engine: Engine) -> None:
    """Run the stdio MCP server against an open database until the client closes it."""
    anyio.run(_serve, engine)


def main() -> None:
    """Console-script entry: open the shared per-user database and serve over stdio.

    The MCP transport has no place to pass ``--db``, so this always opens the
    default file; the CLI's ``tt mcp`` remains the path that honours an override."""
    serve(schema.bootstrap())
