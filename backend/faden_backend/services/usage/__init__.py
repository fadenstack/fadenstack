"""Usage metering as people and administrators read it: the gateway's rollups, behind the privacy controls.

The gateway counts every request into rollups in its own database (``usage_hourly``, ``usage_daily`` without
people, ``usage_daily_person`` per person and day) and purges them on the ``usage.*`` retention settings. This
package reads them:

- ``config``: the ``usage.*`` settings, read live from the system settings at each request.
- ``measures``: the figures every view shows (requests, tokens, estimated cost per currency).
- ``suppression``: the minimum group size k with complementary suppression (M04, M05).
- ``rollups``: the queries against the gateway database.
- ``people``: who may see the usage of named people, and the record of every such view (M02, M06, M07).
- ``statement``: the configuration statement for the works agreement and the DPIA (S09).

M- and S-numbers are the requirements of the compliance report "Usage metering and budgets - GDPR, AI Act,
German law", section 8.
"""
