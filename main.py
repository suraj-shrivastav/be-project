import os

import duckdb
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from models.filter import Filter
from models.guard import GuardModel, SafetyLabel

# silence the huggingface/tokenizers parallelism warning
os.environ["TOKENIZERS_PARALLELISM"] = "false"

console = Console()


def main():
    try:
        with console.status("[bold green]Setting up environment...") as status:
            load_dotenv()
            status.update("[bold green]Waking up filter model...")
            model = Filter()
            console.log("Woke up filter model")
            status.update("[bold green]Waking up guard model...")
            guard = GuardModel()
            console.log("Woke up guard model")
            console.log("SQL validator ready")
            conn = duckdb.connect()
            conn.execute(
                "CREATE VIEW fundamentals AS SELECT * FROM read_parquet('data/fundamentals.parquet');"
            )
            conn.execute(
                "CREATE VIEW prices AS SELECT * FROM read_parquet('data/consolidated/**/*.parquet', hive_partitioning=1);"
            )

        console.print("[bold green]✓ All systems ready![/]")

        while True:
            prompt = console.input("[blue]user: [/blue]")
            if prompt.lower().strip() in {"exit", "quit"}:
                console.print("[bold yellow]Exiting...[/bold yellow]")
                break
            if not prompt.strip():
                console.print("[dim white]No input given[/]")
                continue

            result = process_prompt(prompt, guard, model, conn)
            if result is None:
                continue

    except KeyboardInterrupt:
        console.print("\n[red]Keyboard interrupt received - shutting down...[/red]")
        return
    except Exception as e:
        console.print(f"[red]Unexpected error:[/red] {e}")
        import traceback

        console.print(traceback.format_exc())
        return
    finally:
        conn.close()


def process_prompt(
    prompt: str, guard: GuardModel, model: Filter, conn: duckdb.DuckDBPyConnection
):
    """Process a user prompt through the full pipeline."""

    # Step 1: Safety check
    with console.status("[bold green]Checking safety guidelines...") as status:
        label, categories = guard(prompt)
        if label is not SafetyLabel.Safe:
            status.stop()
            console.log(f"[red]Safety check failed: {label}[/]")
            if categories:
                console.log(f"[blue]Categories: {', '.join(categories)}[/]")
            return None

    console.log("[green]✓ Safety check passed[/]")

    # Step 2: Generate SQL query
    with console.status("[bold green]Generating SQL query...") as status:
        sql_query = model(prompt)
        console.log(f"[yellow]SQL: {sql_query.sql_template}[/]")
        console.log(f"[dim]Parameters: {sql_query.parameters}[/]")

    # Step 3: Validate SQL and handle errors
    if sql_query.error:
        console.log(f"[red]SQL generation error: {sql_query.error}[/]")
        console.log("[yellow]Error type: sql_error[/]")
        return None

    # Step 4: Execute query
    with console.status("[bold green]Executing query...") as status:
        try:
            df = conn.execute(
                sql_query.sql_template, list(sql_query.parameters.values())
            ).fetchdf()

            status.stop()
            console.log(f"[green]✓ Query returned {len(df)} rows[/]")

            if len(df) == 0:
                console.print("[yellow]No results found[/]")
                return None

            # Display results
            display_results(df, sql_query.sql_template)

            return df

        except Exception as e:
            console.log(f"[red]Query execution error: {e}[/]")
            return None


def display_results(df, expression: str):
    """Display query results in a formatted table."""

    console.print("\n" + "=" * 80)
    console.print(f"[bold]Expression:[/bold] {expression}")
    console.print(f"[bold]Results:[/bold] {len(df)} rows found")
    console.print("=" * 80 + "\n")

    # Show first 20 rows max
    display_df = df.head(20)

    # Create a rich table
    table = Table(title="Query Results", show_header=True, header_style="bold magenta")

    # Add columns
    for col in display_df.columns:
        table.add_column(col, justify="right", style="cyan")

    # Add rows
    for _, row in display_df.iterrows():
        row_data = []
        for val in row:
            if isinstance(val, float):
                # Format floats nicely
                if abs(val) >= 1e9:
                    row_data.append(f"{val / 1e9:.2f}B")
                elif abs(val) >= 1e6:
                    row_data.append(f"{val / 1e6:.2f}M")
                elif abs(val) >= 1e3:
                    row_data.append(f"{val / 1e3:.2f}K")
                elif abs(val) < 0.01 and val != 0:
                    row_data.append(f"{val:.4f}")
                else:
                    row_data.append(f"{val:.2f}")
            else:
                row_data.append(str(val))
        table.add_row(*row_data)

    console.print(table)

    if len(df) > 20:
        console.print(f"\n[dim]Showing 20 of {len(df)} rows[/]")


if __name__ == "__main__":
    main()
