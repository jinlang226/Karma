#!/usr/bin/env python3
"""
Data Generator for Spark Data Skew Optimization Task

Generates three tables with data skew for join optimization testing:
- orders: ~50GB equivalent (large fact table)
- products: ~5GB equivalent (skewed dimension table - 10x skew on hot keys)
- customers: ~30GB equivalent (medium dimension table)

For local testing, we use scaled-down versions that maintain the same skew ratios.
"""

import random
import os
from datetime import datetime, timedelta

# Scale factor (1.0 = full size, 0.001 = 1/1000 for testing)
SCALE_FACTOR = float(os.environ.get('SCALE_FACTOR', '0.001'))

# Base record counts (at scale 1.0)
ORDERS_COUNT = int(500_000_000 * SCALE_FACTOR)      # 50GB worth
PRODUCTS_COUNT = int(50_000_000 * SCALE_FACTOR)      # 5GB worth
CUSTOMERS_COUNT = int(300_000_000 * SCALE_FACTOR)    # 30GB worth

# Skew configuration
HOT_PRODUCT_IDS = [1, 2, 3, 4, 5]  # These 5 products will have 10x more orders
SKEW_RATIO = 10  # Hot keys appear 10x more often

OUTPUT_DIR = os.environ.get('OUTPUT_DIR', '/tmp/spark-skew-data')


def generate_orders(output_path):
    """Generate orders table with skewed product_id distribution."""
    print(f"Generating {ORDERS_COUNT} orders...")

    with open(output_path, 'w') as f:
        f.write("order_id,customer_id,product_id,quantity,price,order_date\n")

        for i in range(ORDERS_COUNT):
            order_id = i + 1
            customer_id = random.randint(1, max(1, CUSTOMERS_COUNT))

            # Create skewed product_id distribution
            # 50% of orders go to hot products (5 products get 10x traffic)
            if random.random() < 0.5:
                product_id = random.choice(HOT_PRODUCT_IDS)
            else:
                product_id = random.randint(6, max(6, PRODUCTS_COUNT))

            quantity = random.randint(1, 10)
            price = round(random.uniform(10.0, 1000.0), 2)

            # Random date in last year
            days_ago = random.randint(0, 365)
            order_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')

            f.write(f"{order_id},{customer_id},{product_id},{quantity},{price},{order_date}\n")

            if (i + 1) % 100000 == 0:
                print(f"  Generated {i + 1} orders...")

    print(f"Orders saved to {output_path}")


def generate_products(output_path):
    """Generate products table."""
    print(f"Generating {PRODUCTS_COUNT} products...")

    categories = ['Electronics', 'Clothing', 'Home', 'Sports', 'Books', 'Food', 'Toys', 'Health']

    with open(output_path, 'w') as f:
        f.write("product_id,product_name,category,supplier_id,weight,is_active\n")

        for i in range(PRODUCTS_COUNT):
            product_id = i + 1
            product_name = f"Product_{product_id}"
            category = random.choice(categories)
            supplier_id = random.randint(1, 1000)
            weight = round(random.uniform(0.1, 50.0), 2)
            is_active = random.choice([True, False])

            f.write(f"{product_id},{product_name},{category},{supplier_id},{weight},{is_active}\n")

    print(f"Products saved to {output_path}")


def generate_customers(output_path):
    """Generate customers table."""
    print(f"Generating {CUSTOMERS_COUNT} customers...")

    regions = ['North', 'South', 'East', 'West', 'Central']
    tiers = ['Bronze', 'Silver', 'Gold', 'Platinum']

    with open(output_path, 'w') as f:
        f.write("customer_id,customer_name,region,tier,signup_date,total_spend\n")

        for i in range(CUSTOMERS_COUNT):
            customer_id = i + 1
            customer_name = f"Customer_{customer_id}"
            region = random.choice(regions)
            tier = random.choice(tiers)

            days_ago = random.randint(0, 1825)  # Last 5 years
            signup_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
            total_spend = round(random.uniform(0, 100000.0), 2)

            f.write(f"{customer_id},{customer_name},{region},{tier},{signup_date},{total_spend}\n")

            if (i + 1) % 100000 == 0:
                print(f"  Generated {i + 1} customers...")

    print(f"Customers saved to {output_path}")


def generate_skew_analysis(output_path):
    """Generate a report showing the expected skew distribution."""
    print("Generating skew analysis report...")

    with open(output_path, 'w') as f:
        f.write("Data Skew Analysis Report\n")
        f.write("=" * 60 + "\n\n")

        f.write("Table Sizes (at current scale):\n")
        f.write(f"  - Orders:    {ORDERS_COUNT:,} records\n")
        f.write(f"  - Products:  {PRODUCTS_COUNT:,} records\n")
        f.write(f"  - Customers: {CUSTOMERS_COUNT:,} records\n\n")

        f.write("Skew Configuration:\n")
        f.write(f"  - Hot Product IDs: {HOT_PRODUCT_IDS}\n")
        f.write(f"  - Skew Ratio: {SKEW_RATIO}x\n")
        f.write(f"  - ~50% of orders concentrated on {len(HOT_PRODUCT_IDS)} products\n\n")

        f.write("Expected Distribution:\n")
        hot_orders = int(ORDERS_COUNT * 0.5)
        cold_orders = ORDERS_COUNT - hot_orders
        cold_products = max(1, PRODUCTS_COUNT - len(HOT_PRODUCT_IDS))

        f.write(f"  - Hot products ({len(HOT_PRODUCT_IDS)} products): ~{hot_orders:,} orders total\n")
        f.write(f"    - Per hot product: ~{hot_orders // len(HOT_PRODUCT_IDS):,} orders\n")
        f.write(f"  - Cold products ({cold_products:,} products): ~{cold_orders:,} orders total\n")
        f.write(f"    - Per cold product: ~{cold_orders // cold_products if cold_products > 0 else 0:,} orders\n\n")

        if cold_products > 0:
            skew_factor = (hot_orders / len(HOT_PRODUCT_IDS)) / (cold_orders / cold_products)
            f.write(f"  Actual Skew Factor: {skew_factor:.1f}x\n\n")

        f.write("Optimization Strategies to Consider:\n")
        f.write("  1. Salting: Add random prefix to hot keys, join with exploded dimension\n")
        f.write("  2. Broadcast Join: Broadcast smaller table if it fits in memory\n")
        f.write("  3. Adaptive Query Execution (AQE): Enable spark.sql.adaptive.enabled\n")
        f.write("  4. Skew Join Hint: Use /*+ SKEW('table', 'column') */ hint\n")
        f.write("  5. Two-phase Aggregation: Pre-aggregate before final join\n")

    print(f"Analysis saved to {output_path}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\nData Generation Settings:")
    print(f"  Scale Factor: {SCALE_FACTOR}")
    print(f"  Output Directory: {OUTPUT_DIR}")
    print()

    generate_orders(os.path.join(OUTPUT_DIR, 'orders.csv'))
    generate_products(os.path.join(OUTPUT_DIR, 'products.csv'))
    generate_customers(os.path.join(OUTPUT_DIR, 'customers.csv'))
    generate_skew_analysis(os.path.join(OUTPUT_DIR, 'skew_analysis.txt'))

    print("\nData generation complete!")
    print(f"Files created in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
