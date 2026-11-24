# App
## Importación de librerías

import os
import pandas as pd
import plotly.express as px
import psycopg
from dash import Dash, dcc, html, Input, Output
from dotenv import load_dotenv

# Carga de variables de entorno
load_dotenv()


def get_connection():
    """Crea y retorna una conexión a la base de datos PostgreSQL."""
    db_url = os.getenv("pstgrl")
    if not db_url:
        raise ValueError("La variable de entorno 'pstgrl' no está definida.")

    conn = psycopg.connect(db_url)
    print("¡Conexión exitosa a PostgreSQL!")
    return conn


# Obtener datos
def get_data():
    """Consulta todos los registros de la tabla logs."""
    query = "SELECT * FROM logs"
    with get_connection() as conn:
        df = pd.read_sql(query, conn)
    return df

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Inspecciona y limpia el DataFrame recibido (función pipeline df -> df)."""
    df = df.copy()
    if "status_code" in df.columns:
        df["status_code"] = pd.to_numeric(df["status_code"], errors="coerce")
    df.info()
    return df


def count_errors(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega banderas y conteos acumulados de errores 4xx y 5xx (función pipeline df -> df)."""
    df = df.copy()
    if "status_code" in df.columns:
        status = pd.to_numeric(df["status_code"], errors="coerce")
        df["is_4xx"] = (status >= 400) & (status < 500)
        df["is_5xx"] = (status >= 500) & (status < 600)
    else:
        df["is_4xx"] = False
        df["is_5xx"] = False

    df["error_4xx_count"] = df["is_4xx"].cumsum()
    df["error_5xx_count"] = df["is_5xx"].cumsum()
    return df


def build_error_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Construye un DataFrame resumen con el conteo total de errores 4xx y 5xx."""
    count_4xx = int(df["is_4xx"].sum()) if "is_4xx" in df.columns else 0
    count_5xx = int(df["is_5xx"].sum()) if "is_5xx" in df.columns else 0
    return pd.DataFrame({
        "tipo_error": ["4xx (Cliente)", "5xx (Servidor)"],
        "cantidad": [count_4xx, count_5xx],
    })


def create_app(df: pd.DataFrame) -> Dash:
    """Construye y configura la aplicación Dash."""
    app = Dash(__name__)

    columns = list(df.columns)
    default_col = columns[0] if columns else None

    error_summary = build_error_summary(df)
    count_4xx = int(error_summary.loc[0, "cantidad"])
    count_5xx = int(error_summary.loc[1, "cantidad"])
    total_errors = count_4xx + count_5xx

    error_fig = px.bar(
        error_summary,
        x="tipo_error",
        y="cantidad",
        color="tipo_error",
        text="cantidad",
        title="Conteo de Errores HTTP (4xx vs 5xx)",
        color_discrete_map={
            "4xx (Cliente)": "#f59e0b",
            "5xx (Servidor)": "#ef4444",
        },
    )
    error_fig.update_layout(showlegend=False, xaxis_title="Tipo de Error", yaxis_title="Cantidad")

    card_style = {
        "border": "1px solid #e5e7eb",
        "borderRadius": "8px",
        "padding": "16px 24px",
        "minWidth": "180px",
        "textAlign": "center",
        "boxShadow": "0 1px 3px rgba(0, 0, 0, 0.1)",
    }

    # Layout de la aplicación
    app.layout = html.Div([
        html.H1("Dashboard de Logs"),
        html.Div(
            [
                html.Div(
                    [
                        html.H3("Errores 4xx", style={"margin": "0", "color": "#d97706"}),
                        html.P(str(count_4xx), id="error-4xx-total", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
                html.Div(
                    [
                        html.H3("Errores 5xx", style={"margin": "0", "color": "#dc2626"}),
                        html.P(str(count_5xx), id="error-5xx-total", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
                html.Div(
                    [
                        html.H3("Total Errores", style={"margin": "0", "color": "#374151"}),
                        html.P(str(total_errors), id="error-total", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
            ],
            style={"display": "flex", "gap": "16px", "marginBottom": "24px", "flexWrap": "wrap"},
        ),
        dcc.Graph(id="error-count-graph", figure=error_fig),
        dcc.Graph(id="log-graph"),
        dcc.Dropdown(
            id="log-dropdown",
            options=[{"label": col, "value": col} for col in columns],
            value=default_col,
        ),
    ], style={"padding": "24px", "fontFamily": "sans-serif"})

    # Callback para actualizar el gráfico según la selección del dropdown
    @app.callback(
        Output("log-graph", "figure"),
        Input("log-dropdown", "value"),
    )
    def update_graph(selected_column):
        if not selected_column or selected_column not in df.columns:
            return px.line(title="Sin datos para mostrar")
        fig = px.line(
            df,
            x=df.index,
            y=selected_column,
            title=f"Gráfico de {selected_column}",
        )
        return fig

    return app


def main():
    df = (
        get_data()
        .pipe(clean_data)
        .pipe(count_errors)
    )
    app = create_app(df)
    app.run(debug=True)
    return df


if __name__ == "__main__":
    main()