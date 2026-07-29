# Fabric notebook — refresh the running Claude-usage Delta tables from the CSVs
# the billing engine pushes to ADLS (surfaced in this Lakehouse via a shortcut).
#
# Paste this into a cell of a Fabric notebook attached to the Lakehouse, then
# schedule it to run shortly AFTER the sync job (SYNC_FREQUENCY) fires.
#
# Why overwrite (not merge/append): the engine regenerates the COMPLETE history
# on every sync and writes a single flat CSV per table, so overwriting the Delta
# table each run yields the full, de-duplicated running record — no partitions,
# no duplicate rows.

# Path to the shortcut you created under Files/ pointing at the container folder.
# If you set ADLS_PREFIX empty (files at container root), make this the shortcut
# to the container itself.
BASE = "Files/claude-billing"

TABLES = ["claudeusagesummary", "claudeusagelineitems"]

for name in TABLES:
    df = (spark.read
          .option("header", True)
          .option("inferSchema", True)
          .csv(f"{BASE}/{name}.csv"))
    (df.write
       .mode("overwrite")
       .option("overwriteSchema", "true")
       .saveAsTable(name))
    print(f"refreshed table {name}: {df.count()} row(s)")
