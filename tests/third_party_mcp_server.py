# Serveur MCP "tiers" pour tests/test_mcp.py : un vrai serveur MCP (protocole stdio) qui n'est pas celui de
# SensAI, sans reseau ni cle API. Il lance trois outils : echo (renvoie du JSON), shout (renvoie du texte brut)
# et boom (plante toujours). Il n'est pas un test : unittest ne le decouvre pas (le nom ne commence pas par test_).

from mcp.server import MCPServer

mcp = MCPServer("Tiers", log_level="CRITICAL")


@mcp.tool()
def echo(text: str) -> dict:
    """Renvoie le texte recu, sous forme d'objet JSON."""
    return {"echo": text}


@mcp.tool()
def shout(text: str) -> str:
    """Renvoie le texte recu en majuscules, sous forme de texte brut."""
    return text.upper()


@mcp.tool()
def boom() -> dict:
    """Leve toujours une exception."""
    raise RuntimeError("api HS")


if __name__ == "__main__":
    mcp.run()
