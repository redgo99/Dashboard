# Dashboard de Logs

Aplicación web interactiva construida con **Dash** y **Plotly** para visualizar los registros almacenados en la tabla `logs` de una base de datos **PostgreSQL**.

## Requisitos

- **Python** `>= 3.14`
- **[uv](https://docs.astral.sh/uv/)** para la gestión de dependencias y entorno virtual
- Una instancia de **PostgreSQL** con acceso a la tabla `logs`

## Instalación

1. Clona el repositorio o sitúate en la carpeta del proyecto:
   ```bash
   cd dashboard
   ```

2. Instala las dependencias usando `uv`:
   ```bash
   uv sync
   ```

## Configuración

Crea un archivo `.env` en la raíz del proyecto con la cadena de conexión a PostgreSQL bajo la variable `pstgrl`:

```env
pstgrl=postgresql://usuario:contraseña@host:puerto/base_de_datos
```

## Ejecución

Inicia el servidor de desarrollo de Dash ejecutando:

```bash
uv run python app.py
```

Por defecto, la aplicación se ejecutará en modo debug (`debug=True`) y estará disponible en `http://127.0.0.1:8050/`.

## Funcionalidades

- **Conexión a PostgreSQL** (`get_connection`): lee la URL de conexión desde la variable de entorno `pstgrl` mediante `python-dotenv` y se conecta usando `psycopg`.
- **Carga de datos** (`get_data`): ejecuta `SELECT * FROM logs` y carga los resultados en un `DataFrame` de `pandas`.
- **Inspección de datos** (`clean_data`): muestra el resumen del `DataFrame` (`df.info()`) antes de iniciar la interfaz.
- **Visualización interactiva** (`create_app`):
  - Selector desplegable (`dcc.Dropdown`) con las columnas disponibles de la tabla `logs`.
  - Gráfico de líneas (`plotly.express.line`) que se actualiza dinámicamente según la columna seleccionada.

## Estructura del proyecto

```text
dashboard/
├── app.py              # Script principal de la aplicación Dash
├── pyproject.toml      # Metadatos y dependencias del proyecto
├── uv.lock             # Archivo de bloqueo de dependencias
├── .python-version     # Versión de Python (3.14)
├── .env                # Variables de entorno (no incluir en git)
├── src/
│   └── dashboard/      # Paquete base del proyecto
└── README.md
```

## Dependencias principales

- `dash>=4.5.0`
- `pandas>=3.1.1`
- `psycopg[binary]>=3.3.9`
- `python-dotenv>=1.2.4`
