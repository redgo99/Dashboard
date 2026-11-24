# App
## Importación de librerías

from __future__ import annotations

import logging
import os
from datetime import datetime
import pandas as pd
import plotly.express as px
import psycopg
from dash import Dash, dcc, html, Input, Output, ctx
from dotenv import load_dotenv

# Configuración de logging y variables de entorno
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
load_dotenv()

ALLOWED_HOURS = {1, 3, 24}
BUCKET_BY_HOURS = {1: "1min", 3: "3min", 24: "10min"}


def get_connection():
    """Crea y retorna una conexión a la base de datos PostgreSQL."""
    db_url = os.getenv("pstgrl")
    if not db_url:
        raise ValueError("La variable de entorno 'pstgrl' no está definida.")

    conn = psycopg.connect(db_url)
    logger.debug("Conexión exitosa a PostgreSQL")
    return conn


# Obtener datos
def get_data(hours: int = 24) -> pd.DataFrame:
    """Consulta los registros de la tabla logs dentro de la ventana de tiempo indicada (en horas)."""
    if hours not in ALLOWED_HOURS:
        hours = 24
    query = """
        SELECT id, ts, ingested_at, svc, url, http_method, status_code, log, duration
        FROM logs
        WHERE ts >= (SELECT max(ts) FROM logs) - make_interval(hours => %s::int)
        ORDER BY ts ASC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (hours,))
            rows = cur.fetchall()
            columns = [desc.name for desc in cur.description]
    df = pd.DataFrame(rows, columns=columns)
    logger.debug("Cargados %d registros de logs (ventana: %dh)", len(df), hours)
    return df

# Limpieza de datos
def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia y tipifica el DataFrame recibido (función pipeline df -> df)."""
    df = df.copy()
    if "status_code" in df.columns:
        df["status_code"] = pd.to_numeric(df["status_code"], errors="coerce")
    if "duration" in df.columns:
        df["duration"] = pd.to_numeric(df["duration"], errors="coerce")
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
        df = df.sort_values("ts").reset_index(drop=True)
    logger.debug("DataFrame procesado: %d filas, %d columnas", len(df), len(df.columns))
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
    """Agrupa las peticiones por minuto rellenando con 0 los minutos sin tráfico."""
    if "ts" not in df.columns:
        return pd.DataFrame(columns=["minuto", "rpm"])

    ts = pd.to_datetime(df["ts"], errors="coerce").dropna()
    if ts.empty:
        return pd.DataFrame(columns=["minuto", "rpm"])

    counts = ts.dt.floor("min").value_counts().sort_index()
    full_range = pd.date_range(start=counts.index.min(), end=counts.index.max(), freq="1min")
    rpm_df = (
        counts.reindex(full_range, fill_value=0)
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


def apply_chart_theme(fig, *, height: int = 360, hovermode: str = "x unified"):
    """Aplica un estilo visual uniforme y limpio a los gráficos Plotly."""
    fig.update_layout(
        template="plotly_white",
        height=height,
        hovermode=hovermode,
        hoverlabel=dict(
            bgcolor="rgba(255, 255, 255, 0.96)",
            bordercolor="#cbd5e1",
            font=dict(family="Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", size=12, color="#0f172a"),
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", color="#1e293b", size=12),
        title=dict(font=dict(size=16, color="#0f172a"), x=0.01, xanchor="left"),
        margin=dict(l=52, r=20, t=58, b=44),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, title_text=""),
    )
    fig.update_xaxes(showgrid=False, linecolor="#e2e8f0", tickfont=dict(color="#64748b"))
    fig.update_yaxes(gridcolor="#f1f5f9", zerolinecolor="#e2e8f0", tickfont=dict(color="#64748b"))
    return fig


def build_kpi_card(title: str, value: str, value_id: str, title_color: str) -> html.Div:
    """Construye una tarjeta KPI compacta con título de color, valor oscuro y efecto hover."""
    return html.Div(
        [
            html.H3(title, className="kpi-title", style={"color": title_color}),
            html.P(value, id=value_id, className="kpi-value"),
        ],
        className="kpi-card",
    )


def build_throughput_figure(df: pd.DataFrame):
    """Construye el gráfico de Throughput (requests por minuto) con 0 en minutos sin tráfico."""
    rpm_df = build_throughput_df(df)
    if rpm_df.empty:
        return apply_chart_theme(px.line(title="Throughput (RPM) — Sin datos"))

    fig = px.area(
        rpm_df,
        x="minuto",
        y="rpm",
        markers=True,
        title="Throughput (Requests por Minuto)",
        labels={"minuto": "Tiempo", "rpm": "Requests / min"},
        color_discrete_sequence=["#0284c7"],
    )
    fig.update_traces(line=dict(width=2.5), fillcolor="rgba(2, 132, 199, 0.12)")
    fig.update_layout(xaxis_title="Tiempo", yaxis_title="Requests / min")
    return apply_chart_theme(fig)

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
    fig.update_traces(textposition="outside", marker_line_width=0, width=0.45)
    fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="Cantidad")
    return apply_chart_theme(fig, hovermode="closest")

def fill_short_gaps(series: pd.Series, max_gap: int = 3) -> pd.Series:
    """Interpola los huecos de hasta `max_gap` intervalos seguidos; los huecos más largos quedan vacíos."""
    filled = series.interpolate(limit_area="inside")
    is_na = series.isna()
    run_id = (is_na != is_na.shift()).cumsum()
    run_len = is_na.groupby(run_id).transform("sum")
    return filled.mask(is_na & (run_len > max_gap))


# Construcción de gráfico de tiempo de respuesta
def build_response_time_figure(df: pd.DataFrame, hours: int = 24):
    """Promedia el tiempo de respuesta por servicio en buckets de 1, 3 o 10 min dejando huecos largos vacíos."""
    freq = BUCKET_BY_HOURS.get(hours, "10min")
    if "duration" not in df.columns or df["duration"].dropna().empty:
        return apply_chart_theme(px.line(title=f"Tiempo de Respuesta ({freq}) — Sin datos"))

    if "ts" not in df.columns or df["ts"].dropna().empty:
        return apply_chart_theme(px.line(title=f"Tiempo de Respuesta ({freq}) — Sin datos"))

    work = df.copy()
    work["ts"] = pd.to_datetime(work["ts"], errors="coerce")
    work["duration"] = pd.to_numeric(work["duration"], errors="coerce")
    work = work.dropna(subset=["ts", "duration"])
    if work.empty:
        return apply_chart_theme(px.line(title=f"Tiempo de Respuesta ({freq}) — Sin datos"))

    work["bucket"] = work["ts"].dt.floor(freq)
    full_buckets = pd.date_range(start=work["bucket"].min(), end=work["bucket"].max(), freq=freq)

    if "svc" in work.columns and not work["svc"].dropna().empty:
        grouped = work.groupby(["svc", "bucket"], as_index=False)["duration"].mean()
        services = sorted(grouped["svc"].unique())
        frames = []
        for svc in services:
            svc_series = (
                grouped[grouped["svc"] == svc]
                .set_index("bucket")["duration"]
                .reindex(full_buckets)
                .pipe(fill_short_gaps, max_gap=2)
                .rename_axis("bucket")
                .reset_index()
            )
            svc_series["svc"] = svc
            frames.append(svc_series)
        agg_df = pd.concat(frames, ignore_index=True)
        color_col = "svc"
    else:
        agg_df = (
            work.groupby("bucket")["duration"]
            .mean()
            .reindex(full_buckets)
            .pipe(fill_short_gaps, max_gap=2)
            .rename_axis("bucket")
            .reset_index()
        )
        color_col = None

    fig = px.line(
        agg_df,
        x="bucket",
        y="duration",
        color=color_col,
        line_shape="spline",
        title=f"Tiempo de Respuesta Promedio por Servicio (cada {freq})",
        labels={
            "bucket": "Tiempo",
            "duration": "Duración promedio (ms)",
            "svc": "Servicio",
        },
        color_discrete_sequence=["#2563eb", "#7c3aed", "#0d9488", "#ea580c", "#db2777"],
    )
    if color_col:
        fig.update_traces(
            line=dict(width=2, smoothing=0.6),
            connectgaps=False,
            hovertemplate="%{fullData.name}: <b>%{y:.1f} ms</b><extra></extra>",
        )
    else:
        fig.update_traces(
            line=dict(width=2.2, smoothing=0.6),
            connectgaps=False,
            hovertemplate="Promedio: <b>%{y:.1f} ms</b><extra></extra>",
        )
    fig.update_layout(
        xaxis_title="Tiempo",
        yaxis_title="Duración promedio (ms)",
    )
    fig.update_yaxes(rangemode="tozero")
    return apply_chart_theme(fig, height=380)


def build_column_figure(df: pd.DataFrame, column: str | None, hours: int = 24):
    """Gráfico del explorador de métricas (status_code o duration), agrupado por intervalos de tiempo."""
    freq = BUCKET_BY_HOURS.get(hours, "10min")
    empty = apply_chart_theme(px.line(title="Sin datos para mostrar"), height=340)
    if not column or column not in df.columns or df.empty or "ts" not in df.columns:
        return empty

    work = df[["ts", column]].copy()
    work["ts"] = pd.to_datetime(work["ts"], errors="coerce")
    work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["ts", column])
    if work.empty:
        return empty
    work["bucket"] = work["ts"].dt.floor(freq)

    if column == "status_code":
        work["clase"] = (work["status_code"] // 100).astype(int).astype(str) + "xx"
        counts = work.groupby(["bucket", "clase"], as_index=False).size().rename(columns={"size": "requests"})
        fig = px.bar(
            counts,
            x="bucket",
            y="requests",
            color="clase",
            title=f"Requests por Código de Estado (cada {freq})",
            labels={"bucket": "Tiempo", "requests": "Requests", "clase": "Código"},
            category_orders={"clase": ["2xx", "3xx", "4xx", "5xx"]},
            color_discrete_map={"2xx": "#10b981", "3xx": "#0284c7", "4xx": "#f59e0b", "5xx": "#ef4444"},
        )
        fig.update_traces(
            marker_line_width=0,
            hovertemplate="%{fullData.name}: <b>%{y} req</b><extra></extra>",
        )
        fig.update_layout(barmode="stack", bargap=0.15, xaxis_title="Tiempo", yaxis_title="Requests")
        return apply_chart_theme(fig, height=340)

    agg = "max" if column.endswith("_count") else "mean"
    series = work.groupby("bucket")[column].agg(agg)
    full_buckets = pd.date_range(start=series.index.min(), end=series.index.max(), freq=freq)
    series = series.reindex(full_buckets).pipe(fill_short_gaps).rename_axis("bucket").reset_index()

    is_duration = column == "duration"
    y_label = "Duración promedio (ms)" if is_duration else column
    title = f"Duración Promedio General (cada {freq})" if is_duration else f"Serie de {column} (cada {freq})"
    hover_tpl = "Duración promedio: <b>%{y:.1f} ms</b><extra></extra>" if is_duration else f"{column}: <b>%{{y:.1f}}</b><extra></extra>"

    fig = px.area(
        series,
        x="bucket",
        y=column,
        line_shape="spline",
        title=title,
        labels={"bucket": "Tiempo", column: y_label},
        color_discrete_sequence=["#2563eb"],
    )
    fig.update_traces(
        line=dict(width=2.2, smoothing=0.6),
        fillcolor="rgba(37, 99, 235, 0.10)",
        connectgaps=False,
        hovertemplate=hover_tpl,
    )
    fig.update_layout(xaxis_title="Tiempo", yaxis_title=y_label)
    fig.update_yaxes(rangemode="tozero")
    return apply_chart_theme(fig, height=340)


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
    """Construye y configura la aplicación Dash; el callback inicial llena KPIs y gráficos."""
    app = Dash(__name__)

    state = {"df": df, "hours": default_hours}
    allowed_explorer_cols = ("status_code", "duration")
    explorer_columns = [col for col in allowed_explorer_cols if col in df.columns] or list(allowed_explorer_cols)
    default_col = explorer_columns[0]
    service_options = build_service_options(df)

    # Layout de la aplicación
    app.layout = html.Div(
        [
            dcc.Interval(
                id="auto-refresh-interval",
                interval=10_000,
                n_intervals=0,
                disabled=False,
            ),
            html.Header(
                [
                    html.Div(
                        [
                            html.H1("Dashboard de Logs", className="dashboard-title"),
                            html.P(
                                "Monitoreo en tiempo real de tráfico, errores HTTP y latencia por servicio",
                                className="dashboard-subtitle",
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Span(className="status-dot"),
                            html.Span("Cargando...", id="last-updated"),
                        ],
                        className="status-badge",
                    ),
                ],
                className="dashboard-header",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("Ventana", className="control-label"),
                            dcc.RadioItems(
                                id="time-window-selector",
                                options=[
                                    {"label": "1 h", "value": 1},
                                    {"label": "3 h", "value": 3},
                                    {"label": "24 h", "value": 24},
                                ],
                                value=default_hours,
                                inline=True,
                                className="pill-radio",
                            ),
                        ],
                        className="control-group",
                    ),
                    html.Div(
                        [
                            html.Span("Servicio", className="control-label"),
                            dcc.Dropdown(
                                id="service-filter",
                                options=service_options,
                                value="ALL",
                                clearable=False,
                                className="service-dropdown",
                            ),
                        ],
                        className="control-group",
                    ),
                    html.Div(
                        [
                            html.Span("Auto-refresh", className="control-label"),
                            dcc.RadioItems(
                                id="refresh-interval-selector",
                                options=[
                                    {"label": "10s", "value": 10_000},
                                    {"label": "60s", "value": 60_000},
                                    {"label": "Pausado", "value": 0},
                                ],
                                value=10_000,
                                inline=True,
                                className="pill-radio",
                            ),
                        ],
                        className="control-group",
                    ),
                ],
                className="toolbar-card",
            ),
            html.Div(
                [
                    build_kpi_card("Throughput", "—", "throughput-rpm", "#0284c7"),
                    build_kpi_card("Errores 4xx", "—", "error-4xx-total", "#d97706"),
                    build_kpi_card("Errores 5xx", "—", "error-5xx-total", "#dc2626"),
                    build_kpi_card("Total Errores", "—", "error-total", "#374151"),
                    build_kpi_card("Resp. Promedio", "—", "resp-avg-ms", "#2563eb"),
                    build_kpi_card("Resp. P95", "—", "resp-p95-ms", "#7c3aed"),
                    build_kpi_card("Resp. P99", "—", "resp-p99-ms", "#9333ea"),
                    build_kpi_card("Resp. Máx", "—", "resp-max-ms", "#0f766e"),
                ],
                className="kpi-grid",
            ),
            html.Div(
                [
                    html.Div(
                        dcc.Graph(id="throughput-graph", config={"displayModeBar": False}),
                        className="chart-card",
                    ),
                    html.Div(
                        dcc.Graph(id="error-count-graph", config={"displayModeBar": False}),
                        className="chart-card",
                    ),
                ],
                className="charts-grid-two",
            ),
            html.Div(
                dcc.Graph(id="response-time-graph", config={"displayModeBar": False}),
                className="chart-card",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Explorador de Métricas", className="explorer-title"),
                            dcc.Dropdown(
                                id="log-dropdown",
                                options=[{"label": col, "value": col} for col in explorer_columns],
                                value=default_col,
                                clearable=False,
                                className="explorer-dropdown",
                            ),
                        ],
                        className="explorer-header",
                    ),
                    dcc.Graph(id="log-graph", config={"displayModeBar": False}),
                ],
                className="chart-card",
            ),
        ],
        className="dashboard-container",
    )

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
                logger.exception("Error al recargar los datos")
                status_text = f"Error al actualizar ({datetime.now().strftime('%H:%M:%S')}): {exc}"

        base_df = state["df"]
        svc_options = build_service_options(base_df)
        if selected_service not in {opt["value"] for opt in svc_options}:
            selected_service = "ALL"
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
        r_fig = build_response_time_figure(current_df, hours=hours)

        line_fig = build_column_figure(current_df, selected_column, hours=hours)

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
    debug = os.getenv("DASH_DEBUG", "0") == "1"
    df = load_processed_data(hours=default_hours)
    app = create_app(df, default_hours=default_hours)
    app.run(debug=debug, use_reloader=False)
    return df


if __name__ == "__main__":
    main()
