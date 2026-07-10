import time
from pyspark.sql import SparkSession
from pyspark.sql.functions import count, broadcast

spark = SparkSession.builder.appName("DataSkew-Broadcast").getOrCreate()
spark.sparkContext.setLogLevel("WARN")

orders = spark.range(50000).selectExpr(
    "id as order_id",
    "CASE WHEN id % 2 = 0 THEN (id % 5) + 1 ELSE (id % 95) + 6 END as product_id",
    "CAST(id * 10.5 AS DOUBLE) as amount"
)
products = spark.range(1, 101).selectExpr(
    "id as product_id",
    "concat('Product_', cast(id as string)) as product_name",
    "concat('cat_', cast(((id-1) / 10 + 1) as string)) as category"
)

orders_skewed = orders.repartition(20, "product_id")
sizes = sorted(orders_skewed.rdd.mapPartitions(lambda it: [sum(1 for _ in it)]).collect(), reverse=True)
non_zero = [s for s in sizes if s > 0]
max_s = sizes[0]
min_s = min(non_zero) if non_zero else 1
print("skew_ratio=" + str(round(max_s / min_s, 1)) + "x  max_partition=" + str(max_s) + "  min_partition=" + str(min_s))

t = time.time()
result = (orders_skewed
    .join(broadcast(products), "product_id")
    .groupBy("category").agg(count("order_id").alias("order_count"))
    .orderBy("category"))
result.show()
elapsed = time.time() - t

print("strategy=broadcast  elapsed=" + str(round(elapsed, 2)) + "s")
print("optimization=broadcast_join  benefit=avoids_shuffle_of_large_table  note=products_broadcast_to_all_executors")
spark.stop()
