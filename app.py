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


def create_app(df):
    """Construye y configura la aplicación Dash."""
    app = Dash(__name__)

    columns = list(df.columns)
    default_col = columns[0] if columns else None

    # Layout de la aplicación
    app.layout = html.Div([
        html.H1("Dashboard de Logs"),
        dcc.Graph(id='log-graph'),
        dcc.Dropdown(
            id='log-dropdown',
            options=[{'label': col, 'value': col} for col in columns],
            value=default_col
        )
    ])

    # Callback para actualizar el gráfico según la selección del dropdown
    @app.callback(
        Output('log-graph', 'figure'),
        Input('log-dropdown', 'value')
    )
    def update_graph(selected_column):
        if not selected_column or selected_column not in df.columns:
            return px.line(title="Sin datos para mostrar")
        fig = px.line(
            df,
            x=df.index,
            y=selected_column,
            title=f'Gráfico de {selected_column}'
        )
        return fig

    return app


def clean_data(df):
    """Inspecciona y limpia el DataFrame recibido."""
    df.info()
    return df


def main():
    df = clean_data(get_data())
    app = create_app(df)
    app.run(debug=True)
    return df


if __name__ == "__main__":
    main()