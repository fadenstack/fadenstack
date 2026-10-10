"""The update check: which Fadenstack release is out, and what this install runs.

``release_feed`` reads the signed list of releases (the same file as the CLI's);
``check`` fetches it once a day and keeps it; ``versions`` audits what every part
of the install runs; ``overview`` puts both together for the console. Nothing
here updates anything: administrators are shown the commands to run.
"""
