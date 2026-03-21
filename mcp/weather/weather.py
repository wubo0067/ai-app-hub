from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("weather", log_level="ERROR")

# Constants
NWS_API_BASE = "https://api.weather.gov"
USER_AGENT = "weather-app/1.0"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """Make a request to the NWS API and return the JSON response."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}

    # async with 用于异步上下文管理器，确保在执行异步操作时能够正确地进行资源管理和清理工作。
    # 与普通的 with 语句类似，但它适用于异步函数（async/def 函数）中，可以使用 await 关键字等待异步操作。
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return None


def format_alert(feature: dict) -> str:
    """Format an alert feature into a readable string."""
    props = feature.get("properties", {})
    return f"""
Event: {props.get('event', 'N/A')}
Area: {props.get('areaDesc', 'N/A')}
Severity: {props.get('severity', 'N/A')}
Description: {props.get('description', 'N/A')}
Instructions: {props.get('instruction', 'N/A')}
""".strip()


@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get current weather alerts for a US state.
    Args:
        state (str): The two-letter state code (e.g., 'CA' for California).
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state.upper()}"
    data = await make_nws_request(url)

    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    if not data["features"]:
        return "No active alerts for this state."

    alerts = [format_alert(feature) for feature in data["features"]]
    return "\n\n".join(alerts)


@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get the weather forecast for a specific location.

    Args:
        latitude (float): Latitude of the location.
        longitude (float): Longitude of the location.
    """

    # Step 1: Get the forecast office and grid points
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # Step 2: Get the forecast URL from the points response
    forecast_url = points_data.get("properties", {}).get("forecast")
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch forecast data for this location."

    # Step 3: Format the forecast periods
    periods = forecast_data.get("properties", {}).get("periods", [])
    forecasts = []
    for period in periods[:5]:  # Limit to next 5 periods
        forecasts.append(
            f"""
Period: {period.get('name', 'N/A')}:
Temperature: {period.get('temperature', 'N/A')}°{period.get('temperatureUnit', '')}
Wind: {period.get('windSpeed', 'N/A')} {period.get('windDirection', '')}
Forecast: {period.get('detailedForecast', 'N/A')}
""".strip()
        )
    return "\n\n".join(forecasts)


if __name__ == "__main__":
    """Initialize and run the server."""
    mcp.run(transport="stdio")
