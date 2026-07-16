"""Generate minimal CI corpus PDFs for testing.

This creates two simple, self-contained PDFs with public domain content
suitable for CI testing of the RAG pipeline.
"""

from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

CORPUS_DIR = Path(__file__).parent / "ci_corpus"


def create_python_guide_pdf() -> None:
    """Create a simple Python programming guide PDF."""
    pdf_path = CORPUS_DIR / "Python_Guide_Intro.pdf"

    c = canvas.Canvas(str(pdf_path), pagesize=letter)
    c.setTitle("Introduction to Python Programming")

    y = 750
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Introduction to Python Programming")

    y -= 40
    c.setFont("Helvetica", 12)

    paragraphs = [
        "Python is a high-level, interpreted programming language known for its simplicity",
        "and readability. It was created by Guido van Rossum and first released in 1991.",
        "",
        "Key Features:",
        "- Easy to learn and read syntax",
        "- Dynamically typed language",
        "- Supports multiple programming paradigms: procedural, object-oriented, functional",
        "- Rich standard library with thousands of modules",
        "- Platform independent and runs on Windows, macOS, and Linux",
        "",
        "Data Types:",
        "Python supports several built-in data types:",
        "1. Integers: whole numbers like 42, -10",
        "2. Floats: decimal numbers like 3.14, -2.5",
        "3. Strings: text enclosed in quotes like 'hello' or \"world\"",
        "4. Lists: ordered collections like [1, 2, 3, 4]",
        "5. Dictionaries: key-value pairs like {'name': 'Alice', 'age': 30}",
        "",
        "Functions:",
        "Functions are reusable blocks of code. A simple function example:",
        "def greet(name):",
        "    return f'Hello, {name}!'",
        "",
        "Python 3.12 is the latest stable release as of 2024, featuring improved",
        "performance, better error messages, and enhanced type checking capabilities.",
        "The Python Package Index (PyPI) contains over 500,000 packages available",
        "for installation via the pip package manager."
    ]

    for para in paragraphs:
        if y < 50:
            c.showPage()
            y = 750
            c.setFont("Helvetica", 12)
        c.drawString(50, y, para)
        y -= 20

    c.save()
    print(f"Created {pdf_path}")


def create_database_guide_pdf() -> None:
    """Create a simple database concepts guide PDF."""
    pdf_path = CORPUS_DIR / "Database_Fundamentals.pdf"

    c = canvas.Canvas(str(pdf_path), pagesize=letter)
    c.setTitle("Database Fundamentals")

    y = 750
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "Database Fundamentals")

    y -= 40
    c.setFont("Helvetica", 12)

    paragraphs = [
        "A database is an organized collection of structured data stored in a computer",
        "system. Databases are essential for storing, retrieving, and managing data",
        "efficiently and reliably.",
        "",
        "Types of Databases:",
        "1. Relational Databases: Store data in tables with rows and columns.",
        "   Examples: PostgreSQL, MySQL, Oracle, SQL Server",
        "   Key concept: ACID properties (Atomicity, Consistency, Isolation, Durability)",
        "",
        "2. NoSQL Databases: Flexible schema for unstructured data.",
        "   Examples: MongoDB, Cassandra, Redis",
        "",
        "3. Graph Databases: Store data as nodes and relationships.",
        "   Examples: Neo4j",
        "",
        "PostgreSQL is an advanced open-source relational database system first",
        "released in 1996. It supports JSON, arrays, and full-text search.",
        "",
        "SQL Basics:",
        "SQL (Structured Query Language) is the standard language for database queries:",
        "- SELECT: retrieve data",
        "- INSERT: add new records",
        "- UPDATE: modify existing records",
        "- DELETE: remove records",
        "",
        "Example query:",
        "SELECT name, age FROM users WHERE age > 18 ORDER BY name;",
        "",
        "Indexes improve query performance by creating pointers to data rows.",
        "A database transaction is a sequence of operations treated as a single unit.",
        "If any operation fails, all changes are rolled back to maintain consistency."
    ]

    for para in paragraphs:
        if y < 50:
            c.showPage()
            y = 750
            c.setFont("Helvetica", 12)
        c.drawString(50, y, para)
        y -= 20

    c.save()
    print(f"Created {pdf_path}")


if __name__ == "__main__":
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    create_python_guide_pdf()
    create_database_guide_pdf()
    print(f"CI corpus created in {CORPUS_DIR}/")
