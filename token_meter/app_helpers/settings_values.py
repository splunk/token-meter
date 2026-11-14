"""Settings value validation helpers."""




def _update_message(state, error_code="", latest_revision=""):
    if state == "disabled":
        return "Automatic update checks are off."
    if state == "checking":
        return "Checking the configured Git upstream."
    if state == "updating":
        return "Pulling and reinstalling Token Meter."
    if state == "updated":
        return "Token Meter was updated successfully."
    if state == "available":
        return (
            f"Version {latest_revision} is ready to install."
            if latest_revision else "A new version is ready to install."
        )
    if state == "current":
        return "This installation matches its configured upstream."
    if state == "waiting":
        return "Waiting for the first update check."
    messages = {
        "source_unavailable": "The installed source checkout is unavailable.",
        "git_unavailable": "Git is unavailable, so Token Meter cannot check for updates.",
        "upstream_unavailable": "The source checkout has no usable tracking upstream.",
        "unsupported_update_branch": "Automatic updates require a main checkout tracking a remote main branch.",
        "fetch_failed": "Token Meter could not fetch the configured Git upstream.",
        "inspect_failed": "Token Meter could not compare the installed and upstream revisions.",
        "dirty_checkout": "An update exists, but the source checkout has local changes.",
        "diverged_checkout": "An update exists, but the source checkout has diverged.",
        "launch_failed": "Token Meter could not start the updater.",
        "linux_helper_unavailable": (
            "The installed Linux update helper is unavailable. "
            "Reinstall Token Meter to repair automatic updates."
        ),
        "install_failed": "The update did not install. The existing checkout needs attention.",
        "update_interrupted": "The last update stopped before it finished. Try installing it again.",
    }
    return messages.get(error_code, "Update status is unavailable.")
