from typing import Any, Dict, List, Optional

from .base import InterfaceBase
from ..backend_api import Session


class RouterService(InterfaceBase):
    """
    Interface to the ``routers`` backend service, used to query router routes.
    """

    def get_routes(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Query the routes registered on the server.

        :param name: Optional regex used to filter the routes by name. Note that the server cannot
            return a single route, so the regex should be as specific as possible (e.g. ``^my_route$``)

        :return: A list of route objects (dictionaries) as returned by the server
        """
        response = self.session.send_request(
            "routers",
            "get_routes",
            json={
                **(
                    {"name": name}
                    if name is not None
                    else {}
                ),
            },
        )

        if response.status_code != 200:
            raise ValueError(
                f"Routes request for '{name}' failed with status code {response.status_code}"
                if name
                else f"Routes request failed with status code {response.status_code}"
            )

        return response.json().get("data", {}).get("routes") or []

    def get_route_type(self, name: str) -> str:
        """
        Return the type of a route given its exact name (e.g. ``path`` or ``subdomain``).

        :param name: The exact name of the route

        :return: The route type, or ``path`` if the route does not exist
        """
        routes = self.get_routes(name=f"^{name}$")

        first_route_type = (
            routes[0].get("type")
            if routes
            else None
        )

        return first_route_type or "path"

    def validate_session(self) -> None:
        """
        Validates a session to make sure the user can use this RouterService's features.
        """
        Session.verify_feature_set("advanced")
        if not Session.check_min_server_version("3.26"):
            raise ValueError(
                "Static routes not supported by the server version. "
                "Minimum required version is 3.26"
            )

    def validate_static_route(self, static_route: str) -> None:
        """
        Validate a static route by its name.
        If the static route is not valid, an exception will be raised.

        :param static_route: The static route to validate
        """
        self.validate_session()

        routes = self.get_routes(name=f"^{static_route}$")
        if not routes:
            raise ValueError(f"Static route '{static_route}' does not exist")

        static_route_object = routes[0]
        if not static_route_object.get("enabled", False):
            raise ValueError(f"Static route '{static_route}' is disabled")

        if (
            static_route_object.get("status", "") == "active"
            and not static_route_object.get("load_balancer", {}).get("enabled", False)
        ):
            raise ValueError(f"Static route '{static_route}' is active but not load-balanced")
