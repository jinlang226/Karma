#!/usr/bin/env python3
"""
Spark Data Skew Query Scenarios

This script contains:
1. Baseline query (slow, suffers from data skew)
2. Multiple optimization strategies
3. Performance comparison utilities

Run with: spark-submit skew_queries.py <strategy>
Strategies: baseline, broadcast, salting, aqe, all
"""

import sys
import time
import os
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

DATA_DIR = os.environ.get('DATA_DIR', '/tmp/spark-skew-data')
RESULTS_DIR = os.environ.get('RESULTS_DIR', '/tmp/spark-skew-results')


def create_spark_session(app_name, enable_aqe=False):
    """Create Spark session with appropriate configuration."""
    builder = SparkSession.builder \
        .appName(app_name) \
        .config("spark.sql.shuffle.partitions", "200")

    if enable_aqe:
        builder = builder \
            .config("spark.sql.adaptive.enabled", "true") \
            .config("spark.sql.adaptive.coalescePartitions.enabled", "true") \
            .config("spark.sql.adaptive.skewJoin.enabled", "true") \
            .config("spark.sql.adaptive.skewJoin.skewedPartitionFactor", "5") \
            .config("spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes", "256MB")

    return builder.getOrCreate()


def load_data(spark):
    """Load the three tables from CSV files."""
    orders = spark.read.csv(f"{DATA_DIR}/orders.csv", header=True, inferSchema=True)
    products = spark.read.csv(f"{DATA_DIR}/products.csv", header=True, inferSchema=True)
    customers = spark.read.csv(f"{DATA_DIR}/customers.csv", header=True, inferSchema=True)

    return orders, products, customers


def analyze_skew(orders, products):
    """Analyze and report data skew in the orders table."""
    print("\n" + "=" * 60)
    print("DATA SKEW ANALYSIS")
    print("=" * 60)

    # Count orders per product
    order_counts = orders.groupBy("product_id").count().orderBy(F.desc("count"))

    print("\nTop 10 products by order count:")
    order_counts.show(10)

    # Calculate skew metrics
    stats = order_counts.agg(
        F.avg("count").alias("avg_count"),
        F.max("count").alias("max_count"),
        F.min("count").alias("min_count"),
        F.stddev("count").alias("stddev_count")
    ).collect()[0]

    skew_ratio = stats["max_count"] / stats["avg_count"] if stats["avg_count"] > 0 else 0

    print(f"\nSkew Metrics:")
    print(f"  Average orders per product: {stats['avg_count']:.2f}")
    print(f"  Max orders for single product: {stats['max_count']}")
    print(f"  Min orders for single product: {stats['min_count']}")
    print(f"  Skew Ratio (max/avg): {skew_ratio:.2f}x")

    # Identify hot keys
    threshold = stats["avg_count"] * 5  # Keys with 5x average are "hot"
    hot_keys = order_counts.filter(F.col("count") > threshold)
    print(f"\nHot keys (>5x average): {hot_keys.count()} products")
    hot_keys.show(10)

    return {
        "avg_count": stats["avg_count"],
        "max_count": stats["max_count"],
        "skew_ratio": skew_ratio,
        "hot_key_count": hot_keys.count()
    }


def baseline_query(orders, products, customers):
    """
    Baseline query - 3-way join suffering from data skew.

    This query joins orders with products and customers to calculate
    total revenue per category per region.
    """
    print("\n" + "=" * 60)
    print("BASELINE QUERY (No Optimization)")
    print("=" * 60)

    start_time = time.time()

    result = orders \
        .join(products, "product_id") \
        .join(customers, "customer_id") \
        .groupBy("category", "region") \
        .agg(
            F.sum(F.col("quantity") * F.col("price")).alias("total_revenue"),
            F.count("*").alias("order_count"),
            F.countDistinct("customer_id").alias("unique_customers")
        ) \
        .orderBy(F.desc("total_revenue"))

    # Force execution
    result.cache()
    count = result.count()

    elapsed_time = time.time() - start_time

    print(f"\nResults: {count} rows")
    print(f"Execution time: {elapsed_time:.2f} seconds")
    result.show(10)

    return elapsed_time, result


def broadcast_join_query(orders, products, customers):
    """
    Optimization 1: Broadcast Join

    Broadcast the smaller products table to avoid shuffle on the skewed join.
    """
    print("\n" + "=" * 60)
    print("BROADCAST JOIN OPTIMIZATION")
    print("=" * 60)

    start_time = time.time()

    # Broadcast the products table (smaller dimension table)
    result = orders \
        .join(F.broadcast(products), "product_id") \
        .join(customers, "customer_id") \
        .groupBy("category", "region") \
        .agg(
            F.sum(F.col("quantity") * F.col("price")).alias("total_revenue"),
            F.count("*").alias("order_count"),
            F.countDistinct("customer_id").alias("unique_customers")
        ) \
        .orderBy(F.desc("total_revenue"))

    result.cache()
    count = result.count()

    elapsed_time = time.time() - start_time

    print(f"\nResults: {count} rows")
    print(f"Execution time: {elapsed_time:.2f} seconds")
    result.show(10)

    return elapsed_time, result


def salting_query(orders, products, customers, salt_buckets=10):
    """
    Optimization 2: Salting

    Add a random salt to hot keys to distribute the load across partitions.
    """
    print("\n" + "=" * 60)
    print(f"SALTING OPTIMIZATION (buckets={salt_buckets})")
    print("=" * 60)

    start_time = time.time()

    # Identify hot product IDs (for simplicity, use known hot keys)
    hot_product_ids = [1, 2, 3, 4, 5]

    # Add salt column to orders (random number 0 to salt_buckets-1)
    orders_salted = orders.withColumn(
        "salt",
        F.when(F.col("product_id").isin(hot_product_ids),
               F.floor(F.rand() * salt_buckets))
        .otherwise(F.lit(0))
    ).withColumn(
        "product_id_salted",
        F.concat(F.col("product_id"), F.lit("_"), F.col("salt"))
    )

    # Explode products table for hot keys
    products_exploded = products.withColumn(
        "salt_array",
        F.when(F.col("product_id").isin(hot_product_ids),
               F.array([F.lit(i) for i in range(salt_buckets)]))
        .otherwise(F.array(F.lit(0)))
    ).withColumn(
        "salt",
        F.explode("salt_array")
    ).withColumn(
        "product_id_salted",
        F.concat(F.col("product_id"), F.lit("_"), F.col("salt"))
    ).drop("salt_array")

    # Join with salted keys
    result = orders_salted \
        .join(products_exploded, "product_id_salted") \
        .join(customers, "customer_id") \
        .groupBy("category", "region") \
        .agg(
            F.sum(F.col("quantity") * F.col("price")).alias("total_revenue"),
            F.count("*").alias("order_count"),
            F.countDistinct("customer_id").alias("unique_customers")
        ) \
        .orderBy(F.desc("total_revenue"))

    result.cache()
    count = result.count()

    elapsed_time = time.time() - start_time

    print(f"\nResults: {count} rows")
    print(f"Execution time: {elapsed_time:.2f} seconds")
    result.show(10)

    return elapsed_time, result


def aqe_query(orders, products, customers):
    """
    Optimization 3: Adaptive Query Execution (AQE)

    Uses Spark's built-in adaptive skew join handling.
    Requires spark.sql.adaptive.enabled=true and spark.sql.adaptive.skewJoin.enabled=true
    """
    print("\n" + "=" * 60)
    print("ADAPTIVE QUERY EXECUTION (AQE) OPTIMIZATION")
    print("=" * 60)

    # Note: AQE should be enabled in spark session config
    start_time = time.time()

    result = orders \
        .join(products, "product_id") \
        .join(customers, "customer_id") \
        .groupBy("category", "region") \
        .agg(
            F.sum(F.col("quantity") * F.col("price")).alias("total_revenue"),
            F.count("*").alias("order_count"),
            F.countDistinct("customer_id").alias("unique_customers")
        ) \
        .orderBy(F.desc("total_revenue"))

    result.cache()
    count = result.count()

    elapsed_time = time.time() - start_time

    print(f"\nResults: {count} rows")
    print(f"Execution time: {elapsed_time:.2f} seconds")
    result.show(10)

    return elapsed_time, result


def repartition_query(orders, products, customers):
    """
    Optimization 4: Repartition by join key

    Pre-partition data to reduce skew impact.
    """
    print("\n" + "=" * 60)
    print("REPARTITION OPTIMIZATION")
    print("=" * 60)

    start_time = time.time()

    # Repartition orders by product_id with more partitions
    orders_repartitioned = orders.repartition(500, "product_id")

    result = orders_repartitioned \
        .join(F.broadcast(products), "product_id") \
        .join(customers, "customer_id") \
        .groupBy("category", "region") \
        .agg(
            F.sum(F.col("quantity") * F.col("price")).alias("total_revenue"),
            F.count("*").alias("order_count"),
            F.countDistinct("customer_id").alias("unique_customers")
        ) \
        .orderBy(F.desc("total_revenue"))

    result.cache()
    count = result.count()

    elapsed_time = time.time() - start_time

    print(f"\nResults: {count} rows")
    print(f"Execution time: {elapsed_time:.2f} seconds")
    result.show(10)

    return elapsed_time, result


def two_phase_agg_query(orders, products, customers):
    """
    Optimization 5: Two-phase aggregation

    Pre-aggregate orders before joining with dimension tables.
    """
    print("\n" + "=" * 60)
    print("TWO-PHASE AGGREGATION OPTIMIZATION")
    print("=" * 60)

    start_time = time.time()

    # Phase 1: Pre-aggregate orders by product_id and customer_id
    orders_preagg = orders.groupBy("product_id", "customer_id").agg(
        F.sum(F.col("quantity") * F.col("price")).alias("customer_product_revenue"),
        F.count("*").alias("order_count")
    )

    # Phase 2: Join with dimensions and final aggregation
    result = orders_preagg \
        .join(F.broadcast(products), "product_id") \
        .join(customers, "customer_id") \
        .groupBy("category", "region") \
        .agg(
            F.sum("customer_product_revenue").alias("total_revenue"),
            F.sum("order_count").alias("order_count"),
            F.countDistinct("customer_id").alias("unique_customers")
        ) \
        .orderBy(F.desc("total_revenue"))

    result.cache()
    count = result.count()

    elapsed_time = time.time() - start_time

    print(f"\nResults: {count} rows")
    print(f"Execution time: {elapsed_time:.2f} seconds")
    result.show(10)

    return elapsed_time, result


def run_all_strategies(spark, orders, products, customers):
    """Run all optimization strategies and compare results."""
    results = {}

    # Baseline
    results['baseline'] = baseline_query(orders, products, customers)

    # Broadcast Join
    results['broadcast'] = broadcast_join_query(orders, products, customers)

    # Salting
    results['salting'] = salting_query(orders, products, customers)

    # Repartition
    results['repartition'] = repartition_query(orders, products, customers)

    # Two-phase aggregation
    results['two_phase'] = two_phase_agg_query(orders, products, customers)

    # AQE (requires separate session with AQE enabled)
    spark_aqe = create_spark_session("SkewOptimization-AQE", enable_aqe=True)
    orders_aqe, products_aqe, customers_aqe = load_data(spark_aqe)
    results['aqe'] = aqe_query(orders_aqe, products_aqe, customers_aqe)
    spark_aqe.stop()

    return results


def print_comparison(results, baseline_time):
    """Print performance comparison table."""
    print("\n" + "=" * 60)
    print("PERFORMANCE COMPARISON")
    print("=" * 60)
    print(f"\n{'Strategy':<20} {'Time (s)':<12} {'Speedup':<12} {'Improvement':<12}")
    print("-" * 56)

    for strategy, (elapsed_time, _) in sorted(results.items(), key=lambda x: x[1][0]):
        speedup = baseline_time / elapsed_time if elapsed_time > 0 else 0
        improvement = ((baseline_time - elapsed_time) / baseline_time) * 100 if baseline_time > 0 else 0
        print(f"{strategy:<20} {elapsed_time:<12.2f} {speedup:<12.2f}x {improvement:<12.1f}%")

    print()

    # Find best strategy
    best_strategy = min(results.items(), key=lambda x: x[1][0])
    best_improvement = ((baseline_time - best_strategy[1][0]) / baseline_time) * 100

    print(f"Best Strategy: {best_strategy[0]}")
    print(f"Best Improvement: {best_improvement:.1f}%")

    return best_strategy[0], best_improvement


def save_results(results, baseline_time, skew_metrics, output_path):
    """Save performance results to file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w') as f:
        f.write("Spark Data Skew Optimization Results\n")
        f.write("=" * 60 + "\n\n")

        f.write("Skew Metrics:\n")
        f.write(f"  Skew Ratio: {skew_metrics['skew_ratio']:.2f}x\n")
        f.write(f"  Hot Key Count: {skew_metrics['hot_key_count']}\n\n")

        f.write("Performance Results:\n")
        f.write(f"{'Strategy':<20} {'Time (s)':<12} {'Speedup':<12} {'Improvement':<12}\n")
        f.write("-" * 56 + "\n")

        for strategy, (elapsed_time, _) in sorted(results.items(), key=lambda x: x[1][0]):
            speedup = baseline_time / elapsed_time if elapsed_time > 0 else 0
            improvement = ((baseline_time - elapsed_time) / baseline_time) * 100 if baseline_time > 0 else 0
            f.write(f"{strategy:<20} {elapsed_time:<12.2f} {speedup:<12.2f}x {improvement:<12.1f}%\n")

        best_strategy = min(results.items(), key=lambda x: x[1][0])
        best_improvement = ((baseline_time - best_strategy[1][0]) / baseline_time) * 100

        f.write(f"\nBest Strategy: {best_strategy[0]}\n")
        f.write(f"Best Improvement: {best_improvement:.1f}%\n")

    print(f"\nResults saved to: {output_path}")


def main():
    strategy = sys.argv[1] if len(sys.argv) > 1 else "all"

    print(f"\nRunning strategy: {strategy}")
    print(f"Data directory: {DATA_DIR}")

    # Create Spark session
    spark = create_spark_session("SkewOptimization-Baseline")

    # Load data
    orders, products, customers = load_data(spark)

    # Analyze skew
    skew_metrics = analyze_skew(orders, products)

    if strategy == "all":
        results = run_all_strategies(spark, orders, products, customers)
        baseline_time = results['baseline'][0]
        best_strategy, best_improvement = print_comparison(results, baseline_time)
        save_results(results, baseline_time, skew_metrics, f"{RESULTS_DIR}/performance_results.txt")
    elif strategy == "baseline":
        baseline_query(orders, products, customers)
    elif strategy == "broadcast":
        broadcast_join_query(orders, products, customers)
    elif strategy == "salting":
        salting_query(orders, products, customers)
    elif strategy == "aqe":
        spark.stop()
        spark = create_spark_session("SkewOptimization-AQE", enable_aqe=True)
        orders, products, customers = load_data(spark)
        aqe_query(orders, products, customers)
    elif strategy == "repartition":
        repartition_query(orders, products, customers)
    elif strategy == "two_phase":
        two_phase_agg_query(orders, products, customers)
    else:
        print(f"Unknown strategy: {strategy}")
        print("Available: baseline, broadcast, salting, aqe, repartition, two_phase, all")
        sys.exit(1)

    spark.stop()


if __name__ == "__main__":
    main()
