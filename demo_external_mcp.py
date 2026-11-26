import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from mcp import StdioServerParameters

from src import agent
from src.config import load_config, set_active_config

FILENAME = "test.txt"
DIRECTORY = os.path.realpath(tempfile.mkdtemp())
CONTENT_FILE = "TEST 123"

async def main():
    config = load_config()
    set_active_config(config)

    Path(DIRECTORY, FILENAME).write_text(CONTENT_FILE, encoding="utf-8")

    agent.server = StdioServerParameters(
        command=shutil.which("npx"),
        args=["-y", "@modelcontextprotocol/server-filesystem", DIRECTORY],
    )

    try:
        client = await agent.connect_mcp()
        print("1. Connecté au MCP Filesystem")

        tools = (await client.list_tools()).tools
        print(f"\n2. Outils découverts ({len(tools)}) :\n\n- {'- '.join(t.name + "\n" for t in tools)}")

        for t in tools:
            config.tools.append({"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema}})

        print("3. execute_tool('list_directory')\n")
        liste = await agent.execute_tool("list_directory", {"path": DIRECTORY})
        files = [file.replace('\n', '').strip() for file in liste.split("[FILE] ")]

        while '' in files:
            files.pop(files.index(''))

        print(f"Fichier trouvé dans {DIRECTORY} :\n- {'- '.join(file + "\n" for file in files)}")

        assert FILENAME in files, "Le fichier n'a pas été créé"

        print("4. execute_tool('read_text_file')\n")
        contenu = await agent.execute_tool("read_text_file", {"path": f"{DIRECTORY}/{FILENAME}"})
        print(f"Contenu souhaité :\n{CONTENT_FILE}\n-----------------\nContenu trouvé :\n{contenu}")

        assert contenu == CONTENT_FILE, "Le contenu du fichier ne correspond pas"

        print("\nOK : un MCP existant fonctionne sur l'architecture de SensAI.")
    finally:
        await agent.close_mcp()
        if DIRECTORY.startswith("/tmp/"):
            shutil.rmtree(DIRECTORY, ignore_errors=True)
        else:
            res = str(input(f"Êtes-vous sur de vouloir supprimer le dossier {DIRECTORY} ainsi que l'intégralité de son contenu ? (N/y) : "))

            if res.strip() == "" or res.strip().upper() == "N":
                print(f"Dossier {DIRECTORY} non supprimé")
            elif res.strip().upper() == "Y":
                shutil.rmtree(DIRECTORY, ignore_errors=True)
                print(f"Le dossier {DIRECTORY} a bien été supprimé")


try:
    asyncio.run(main())
except AssertionError as assertError:
    print(f"Erreur test MCP: {assertError}")
except FileNotFoundError as fnfError:
    print(f"Erreur test MCP: Le dossier {DIRECTORY} n'existe pas")
except Exception as e:
    print(f"Erreur inattendu: {e}")