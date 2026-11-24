# App
## Importación de librerías

import os
from datetime import datetime
import pandas as pd
import plotly.express as px
import psycopg
from dash import Dash, dcc, html, Input, Output, ctx
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


ALLOWED_HOURS = {1, 3, 24}


# Obtener datos
def get_data(hours: int = 24) -> pd.DataFrame:
    """Consulta los registros de la tabla logs dentro de la ventana de tiempo indicada (en horas)."""
    if hours not in ALLOWED_HOURS:
        hours = 24
    query = """
        SELECT id, ts, ingested_at, svc, url, http_method, status_code, log, duration
        FROM logs
        WHERE ts >= now() - (%s * interval '1 hour')
        ORDER BY ts ASC
    """
    with get_connection() as conn:
        df = pd.read_sql(query, conn, params=(hours,))
    return df

#Limpieza de datos
def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Inspecciona y limpia el DataFrame recibido (función pipeline df -> df)."""
    df = df.copy()
    if "status_code" in df.columns:
        df["status_code"] = pd.to_numeric(df["status_code"], errors="coerce")
    if "duration" in df.columns:
        df["duration"] = pd.to_numeric(df["duration"], errors="coerce")
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
    df.info()
    return df

# Conteo de errores
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

# Métricas de tiempo de respuesta
def add_response_time_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega métricas derivadas del tiempo de respuesta en ms (función pipeline df -> df)."""
    df = df.copy()
    if "duration" in df.columns:
        duration = pd.to_numeric(df["duration"], errors="coerce")
        df["duration_rolling_avg"] = duration.rolling(window=10, min_periods=1).mean()
    else:
        df["duration_rolling_avg"] = pd.NA
    return df

# Construcción de resúmenes y gráficos
def build_error_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Construye un DataFrame resumen con el conteo total de errores 4xx y 5xx."""
    count_4xx = int(df["is_4xx"].sum()) if "is_4xx" in df.columns else 0
    count_5xx = int(df["is_5xx"].sum()) if "is_5xx" in df.columns else 0
    return pd.DataFrame({
        "tipo_error": ["4xx (Cliente)", "5xx (Servidor)"],
        "cantidad": [count_4xx, count_5xx],
    })

# Filtro por servicio (función pipeline df -> df)
def filter_by_service(df: pd.DataFrame, service: str | None = None) -> pd.DataFrame:
    """Filtra el DataFrame por servicio (svc) si se selecciona uno específico."""
    df = df.copy()
    if not service or service == "ALL" or "svc" not in df.columns:
        return df
    return df[df["svc"] == service].reset_index(drop=True)


def build_service_options(df: pd.DataFrame) -> list[dict[str, str]]:
    """Construye las opciones del dropdown de servicios a partir de la columna svc."""
    options = [{"label": "Todos los servicios", "value": "ALL"}]
    if "svc" in df.columns:
        services = sorted(df["svc"].dropna().astype(str).unique())
        options.extend({"label": svc, "value": svc} for svc in services)
    return options


# Construcción de resúmenes de tiempo de respuesta
def build_response_time_summary(df: pd.DataFrame) -> dict[str, float]:
    """Calcula estadísticas clave (promedio, P95, P99 y máximo) del tiempo de respuesta en ms."""
    if "duration" not in df.columns:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}

    valid = pd.to_numeric(df["duration"], errors="coerce").dropna()
    if valid.empty:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0}

    return {
        "avg_ms": float(valid.mean()),
        "p95_ms": float(valid.quantile(0.95)),
        "p99_ms": float(valid.quantile(0.99)),
        "max_ms": float(valid.max()),
    }


# Construcción de resúmenes y gráficos de Throughput (RPM)
def build_throughput_df(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa las peticiones por minuto para calcular el Throughput (RPM)."""
    if "ts" not in df.columns:
        return pd.DataFrame(columns=["minuto", "rpm"])

    ts = pd.to_datetime(df["ts"], errors="coerce").dropna()
    if ts.empty:
        return pd.DataFrame(columns=["minuto", "rpm"])

    rpm_df = (
        ts.dt.floor("min")
        .value_counts()
        .sort_index()
        .rename_axis("minuto")
        .reset_index(name="rpm")
    )
    return rpm_df


def build_throughput_summary(df: pd.DataFrame) -> dict[str, float]:
    """Calcula el Throughput promedio y pico en requests por minuto (RPM)."""
    rpm_df = build_throughput_df(df)
    if rpm_df.empty:
        return {"avg_rpm": 0.0, "peak_rpm": 0.0}
    return {
        "avg_rpm": float(rpm_df["rpm"].mean()),
        "peak_rpm": float(rpm_df["rpm"].max()),
    }


def build_throughput_figure(df: pd.DataFrame):
    """Construye el gráfico de Throughput (requests por minuto)."""
    rpm_df = build_throughput_df(df)
    if rpm_df.empty:
        return px.line(title="Throughput (RPM) (sin datos)")

    fig = px.line(
        rpm_df,
        x="minuto",
        y="rpm",
        markers=True,
        title="Throughput (Requests por Minuto - RPM)",
        labels={"minuto": "Tiempo", "rpm": "Requests / min"},
    )
    fig.update_layout(xaxis_title="Tiempo", yaxis_title="Requests / min")
    return fig

# Construcción de gráficos
def build_error_figure(error_summary: pd.DataFrame):
    """Construye el gráfico de barras de errores 4xx vs 5xx."""
    fig = px.bar(
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
    fig.update_layout(showlegend=False, xaxis_title="Tipo de Error", yaxis_title="Cantidad")
    return fig

# Construcción de gráfico de tiempo de respuesta
def build_response_time_figure(df: pd.DataFrame):
    """Construye el gráfico de evolución del tiempo de respuesta (duration en ms)."""
    if "duration" not in df.columns or df["duration"].dropna().empty:
        return px.line(title="Tiempo de Respuesta (sin datos)")

    x_col = "ts" if "ts" in df.columns and not df["ts"].dropna().empty else None
    color_col = "svc" if "svc" in df.columns else None
    fig = px.line(
        df,
        x=x_col if x_col else df.index,
        y="duration",
        color=color_col,
        title="Tiempo de Respuesta (ms)",
        labels={
            "ts": "Tiempo",
            "duration": "Duración (ms)",
            "svc": "Servicio",
        },
    )
    fig.update_layout(
        xaxis_title="Tiempo" if x_col else "Registro",
        yaxis_title="Duración (ms)",
    )
    return fig

# Pipeline completo de carga y procesamiento de datos
def load_processed_data(hours: int = 24) -> pd.DataFrame:
    """Ejecuta el pipeline completo de carga, limpieza, conteo de errores y tiempo de respuesta."""
    return (
        get_data(hours=hours)
        .pipe(clean_data)
        .pipe(count_errors)
        .pipe(add_response_time_metrics)
    )


# Construcción de la aplicación Dash
def create_app(df: pd.DataFrame, data_loader=load_processed_data, default_hours: int = 24) -> Dash:
    """Construye y configura la aplicación Dash."""
    app = Dash(__name__)

    state = {"df": df, "hours": default_hours}
    columns = list(df.columns)
    default_col = columns[0] if columns else None
    service_options = build_service_options(df)

    error_summary = build_error_summary(df)
    count_4xx = int(error_summary.loc[0, "cantidad"])
    count_5xx = int(error_summary.loc[1, "cantidad"])
    total_errors = count_4xx + count_5xx
    error_fig = build_error_figure(error_summary)

    resp_summary = build_response_time_summary(df)
    resp_fig = build_response_time_figure(df)

    throughput_summary = build_throughput_summary(df)
    throughput_fig = build_throughput_figure(df)
    initial_updated = f"Última actualización: {datetime.now().strftime('%H:%M:%S')}"

    card_style = {
        "border": "1px solid #e5e7eb",
        "borderRadius": "8px",
        "padding": "16px 24px",
        "minWidth": "160px",
        "textAlign": "center",
        "boxShadow": "0 1px 3px rgba(0, 0, 0, 0.1)",
    }

    # Layout de la aplicación
    app.layout = html.Div([
        dcc.Interval(
            id="auto-refresh-interval",
            interval=10_000,
            n_intervals=0,
            disabled=False,
        ),
        html.Div(
            [
                html.H1("Dashboard de Logs", style={"margin": "0"}),
                html.Div(
                    [
                        html.Label("Ventana:", style={"fontWeight": "bold"}),
                        dcc.RadioItems(
                            id="time-window-selector",
                            options=[
                                {"label": "1 h", "value": 1},
                                {"label": "3 h", "value": 3},
                                {"label": "24 h", "value": 24},
                            ],
                            value=default_hours,
                            inline=True,
                            style={"display": "flex", "gap": "10px"},
                        ),
                        html.Label("Servicio:", style={"fontWeight": "bold"}),
                        dcc.Dropdown(
                            id="service-filter",
                            options=service_options,
                            value="ALL",
                            clearable=False,
                            style={"minWidth": "200px"},
                        ),
                        html.Label("Auto-refresh:", style={"fontWeight": "bold"}),
                        dcc.RadioItems(
                            id="refresh-interval-selector",
                            options=[
                                {"label": "10s", "value": 10_000},
                                {"label": "60s", "value": 60_000},
                                {"label": "Pausado", "value": 0},
                            ],
                            value=10_000,
                            inline=True,
                            style={"display": "flex", "gap": "10px"},
                        ),
                        html.Span(
                            initial_updated,
                            id="last-updated",
                            style={"color": "#6b7280", "fontSize": "14px"},
                        ),
                    ],
                    style={"display": "flex", "alignItems": "center", "gap": "16px", "flexWrap": "wrap"},
                ),
            ],
            style={
                "display": "flex",
                "justifyContent": "space-between",
                "alignItems": "center",
                "flexWrap": "wrap",
                "gap": "16px",
                "marginBottom": "20px",
            },
        ),
        html.Div(
            [
                html.Div(
                    [
                        html.H3("Throughput", style={"margin": "0", "color": "#0284c7"}),
                        html.P(f"{throughput_summary['avg_rpm']:.1f} RPM", id="throughput-rpm", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
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
                html.Div(
                    [
                        html.H3("Resp. Promedio", style={"margin": "0", "color": "#2563eb"}),
                        html.P(f"{resp_summary['avg_ms']:.1f} ms", id="resp-avg-ms", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
                html.Div(
                    [
                        html.H3("Resp. P95", style={"margin": "0", "color": "#7c3aed"}),
                        html.P(f"{resp_summary['p95_ms']:.1f} ms", id="resp-p95-ms", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
                html.Div(
                    [
                        html.H3("Resp. P99", style={"margin": "0", "color": "#9333ea"}),
                        html.P(f"{resp_summary['p99_ms']:.1f} ms", id="resp-p99-ms", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
                html.Div(
                    [
                        html.H3("Resp. Máx", style={"margin": "0", "color": "#0f766e"}),
                        html.P(f"{resp_summary['max_ms']:.0f} ms", id="resp-max-ms", style={"fontSize": "28px", "fontWeight": "bold", "margin": "8px 0 0"}),
                    ],
                    style=card_style,
                ),
            ],
            style={"display": "flex", "gap": "16px", "marginBottom": "24px", "flexWrap": "wrap"},
        ),
        dcc.Graph(id="throughput-graph", figure=throughput_fig),
        dcc.Graph(id="error-count-graph", figure=error_fig),
        dcc.Graph(id="response-time-graph", figure=resp_fig),
        dcc.Graph(id="log-graph"),
        dcc.Dropdown(
            id="log-dropdown",
            options=[{"label": col, "value": col} for col in columns],
            value=default_col,
        ),
    ], style={"padding": "24px", "fontFamily": "sans-serif"})

    # Callback para cambiar el intervalo de actualización automática (10s / 60s / Pausado)
    @app.callback(
        Output("auto-refresh-interval", "interval"),
        Output("auto-refresh-interval", "disabled"),
        Input("refresh-interval-selector", "value"),
    )
    def update_refresh_interval(selected_interval):
        if not selected_interval:
            return 10_000, True
        return int(selected_interval), False

    # Callback para refrescar datos, filtrar por ventana/servicio y actualizar gráficos/KPIs
    @app.callback(
        Output("throughput-rpm", "children"),
        Output("error-4xx-total", "children"),
        Output("error-5xx-total", "children"),
        Output("error-total", "children"),
        Output("resp-avg-ms", "children"),
        Output("resp-p95-ms", "children"),
        Output("resp-p99-ms", "children"),
        Output("resp-max-ms", "children"),
        Output("throughput-graph", "figure"),
        Output("error-count-graph", "figure"),
        Output("response-time-graph", "figure"),
        Output("log-graph", "figure"),
        Output("service-filter", "options"),
        Output("last-updated", "children"),
        Input("auto-refresh-interval", "n_intervals"),
        Input("time-window-selector", "value"),
        Input("service-filter", "value"),
        Input("log-dropdown", "value"),
    )
    def update_dashboard(n_intervals, selected_hours, selected_service, selected_column):
        hours = int(selected_hours) if selected_hours in ALLOWED_HOURS else default_hours
        status_text = f"Última actualización: {datetime.now().strftime('%H:%M:%S')}"

        should_reload = (
            (ctx.triggered_id == "auto-refresh-interval" and n_intervals > 0)
            or (ctx.triggered_id == "time-window-selector" and hours != state["hours"])
        )
        if should_reload:
            try:
                state["df"] = data_loader(hours)
                state["hours"] = hours
            except Exception as exc:
                status_text = f"Error al actualizar ({datetime.now().strftime('%H:%M:%S')}): {exc}"

        base_df = state["df"]
        svc_options = build_service_options(base_df)
        current_df = (
            base_df
            .pipe(filter_by_service, selected_service)
            .pipe(count_errors)
            .pipe(add_response_time_metrics)
        )

        t_summary = build_throughput_summary(current_df)
        t_fig = build_throughput_figure(current_df)

        summary = build_error_summary(current_df)
        c4xx = int(summary.loc[0, "cantidad"])
        c5xx = int(summary.loc[1, "cantidad"])
        total = c4xx + c5xx
        err_fig = build_error_figure(summary)

        r_summary = build_response_time_summary(current_df)
        r_fig = build_response_time_figure(current_df)

        if not selected_column or selected_column not in current_df.columns:
            line_fig = px.line(title="Sin datos para mostrar")
        else:
            line_fig = px.line(
                current_df,
                x=current_df.index,
                y=selected_column,
                title=f"Gráfico de {selected_column}",
            )

        return (
            f"{t_summary['avg_rpm']:.1f} RPM",
            str(c4xx),
            str(c5xx),
            str(total),
            f"{r_summary['avg_ms']:.1f} ms",
            f"{r_summary['p95_ms']:.1f} ms",
            f"{r_summary['p99_ms']:.1f} ms",
            f"{r_summary['max_ms']:.0f} ms",
            t_fig,
            err_fig,
            r_fig,
            line_fig,
            svc_options,
            status_text,
        )

    return app


def main():
    default_hours = 24
    df = load_processed_data(hours=default_hours)
    app = create_app(df, default_hours=default_hours)
    app.run(debug=True)
    return df


if __name__ == "__main__":
    main()