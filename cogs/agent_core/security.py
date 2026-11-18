from __future__ import annotations
import ast
from typing import List, Tuple, Set


ALLOWED_ROOT_MODULES: Set[str] = {
    "discord",
    "typing",
    "asyncio",
    "random",
    "math",
    "time",
    "datetime",
    "re",
    "json",
    "collections",
    "itertools",
    "functools",
    "string",
    "enum",
    "assets",
}

ALLOWED_INTERNAL_PREFIXES: Tuple[str, ...] = (
    "cogs.economy",
    "cogs.games.helpers",
    "cogs.games.common",
    "assets.wordle_words",
)

DISALLOWED_CALLS: Set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "globals",
    "locals",
    "input",
    "exit",
    "quit",
}

DISALLOWED_ATTRIBUTES: Set[str] = {
    "__subclasses__",
    "__bases__",
    "__mro__",
    "__globals__",
    "__code__",
    "token",
    "_token",
    "environ",
}


class ASTSecurityVisitor(ast.NodeVisitor):
    def __init__(self):
        self.issues: List[str] = []
        self.has_cog_class: bool = False
        self.has_setup_func: bool = False

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            root_mod = alias.name.split(".")[0]
            if root_mod not in ALLOWED_ROOT_MODULES:
                self.issues.append(f"Disallowed import: '{alias.name}'. Only safe Discord & math/utility libraries are permitted.")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        root_mod = mod.split(".")[0]
        is_internal_allowed = any(mod == prefix or mod.startswith(f"{prefix}.") for prefix in ALLOWED_INTERNAL_PREFIXES)
        
        if root_mod not in ALLOWED_ROOT_MODULES and not is_internal_allowed:
            self.issues.append(f"Disallowed import source: '{mod}'.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name):
            if node.func.id in DISALLOWED_CALLS:
                self.issues.append(f"Forbidden function call: '{node.func.id}()'.")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in DISALLOWED_ATTRIBUTES:
            self.issues.append(f"Forbidden attribute access: '.{node.attr}'.")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        # Look for Cog inheritance
        for base in node.bases:
            if isinstance(base, ast.Attribute) and base.attr == "Cog":
                self.has_cog_class = True
            elif isinstance(base, ast.Name) and base.id == "Cog":
                self.has_cog_class = True
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name == "setup":
            self.has_setup_func = True
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        if node.name == "setup":
            self.has_setup_func = True
        self.generic_visit(node)


def validate_python_ast(code: str) -> Tuple[bool, List[str]]:
    """
    Parses Python code into an AST and verifies security constraints.
    Returns (is_safe, list_of_issues).
    """
    issues: List[str] = []
    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [f"SyntaxError on line {e.lineno}: {e.msg}"]
    except Exception as e:
        return False, [f"Failed to parse AST: {str(e)}"]

    visitor = ASTSecurityVisitor()
    visitor.visit(tree)
    issues.extend(visitor.issues)

    if not visitor.has_cog_class:
        issues.append("Missing Cog class: Code must define at least one class inheriting from commands.Cog.")
    if not visitor.has_setup_func:
        issues.append("Missing setup function: Code must define 'async def setup(bot):' to load into discord.py.")

    return (len(issues) == 0, issues)
