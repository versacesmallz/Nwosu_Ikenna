"""
MIT 8103 - Portfolio 4 (part C): the same product documents through the MongoDB API.

The documents built in PostgreSQL JSONB (document_model.sql) are loaded into a MongoDB-API
collection and the same business queries are written as MongoDB find()/aggregate() calls.
The results are compared with the JSONB results to show the model is portable.

Engine note: this build environment could not download MongoDB server binaries, so the run
uses `mongomock`, an in-memory implementation of the PyMongo API. To run against a real
MongoDB (local or Atlas) change ONE line:
    client = pymongo.MongoClient("mongodb://localhost:27017")      # instead of mongomock.MongoClient()
Equivalent mongosh commands are printed beside each query.

Usage: python3 mongo_catalogue.py > evidence/mongo_api_output.txt
"""
import json, os
import psycopg2, mongomock, pymongo

pg = psycopg2.connect(os.environ.get("KASUWA_DSN", "host=localhost dbname=kasuwa user=postgres"))
cur = pg.cursor()

client = mongomock.MongoClient()            # <- swap for pymongo.MongoClient(...) on a real server
db = client["kasuwa"]
products = db["products"]
products.drop()

cur.execute("SELECT doc::text FROM kasuwa_doc.product_catalogue")
docs = [json.loads(r[0]) for r in cur.fetchall()]
res = products.insert_many(docs)
print(f"Inserted {len(res.inserted_ids)} product documents into kasuwa.products")
products.create_index([("category.name", 1), ("attributes.ram_gb", 1), ("price", 1)], name="ix_cat_ram_price")
products.create_index([("attributes.sizes", 1)], name="ix_sizes_multikey")
print("Indexes:", sorted(products.index_information().keys()))


def banner(t, shell):
    print("\n" + "=" * 90 + f"\n{t}\nmongosh> {shell}\n" + "-" * 90)


# D1 ---------------------------------------------------------------------------
banner("D1  5G phones, RAM >= 8 GB, under N500,000, best rated first",
       'db.products.find({"category.name":"Mobile Phones","attributes.network":"5G","active":true,'
       '"attributes.ram_gb":{$gte:8},"price":{$lt:500000}}).sort({"rating.avg":-1}).limit(5)')
d1 = list(products.find(
    {"category.name": "Mobile Phones", "attributes.network": "5G", "active": True,
     "attributes.ram_gb": {"$gte": 8}, "price": {"$lt": 500000}},
    {"_id": 1, "name": 1, "price": 1, "attributes.ram_gb": 1, "rating.avg": 1})
    .sort([("rating.avg", pymongo.DESCENDING), ("_id", pymongo.ASCENDING)]).limit(5))
for d in d1:
    print(f"  {d['_id']}  {d['name']:<32} N{d['price']:>12,.2f}  ram {d['attributes']['ram_gb']:>2} GB  rating {d['rating']['avg']}")

# D2 ---------------------------------------------------------------------------
banner("D2  aggregation pipeline: phone count and average price per brand",
       'db.products.aggregate([{$match:{"category.name":"Mobile Phones"}},'
       '{$group:{_id:"$attributes.brand",phones:{$sum:1},avg_price:{$avg:"$price"}}},{$sort:{avg_price:-1}}])')
d2 = list(products.aggregate([
    {"$match": {"category.name": "Mobile Phones"}},
    {"$group": {"_id": "$attributes.brand", "phones": {"$sum": 1}, "avg_price": {"$avg": "$price"}}},
    {"$sort": {"avg_price": -1}}]))
for g in d2:
    print(f"  {g['_id']:<8} phones={g['phones']:>3}  avg_price=N{round(g['avg_price']):>9,}")

# D3 ---------------------------------------------------------------------------
banner("D3  Ankara gowns in size 12 AND colour Green (query into arrays)",
       'db.products.countDocuments({"category.name":"Women\'s Clothing","attributes.sizes":"12","attributes.colours":"Green"})')
d3 = products.count_documents({"category.name": "Women's Clothing", "attributes.sizes": "12", "attributes.colours": "Green"})
print(f"  matching documents: {d3}")

# D5 ---------------------------------------------------------------------------
banner("D5  $unwind embedded reviews: share of 1-star reviews among latest reviews, per seller state",
       'db.products.aggregate([{$unwind:"$recent_reviews"},{$group:{_id:"$seller.state",'
       'reviews:{$sum:1},one_star:{$sum:{$cond:[{$eq:["$recent_reviews.rating",1]},1,0]}}}},{$sort:{reviews:-1}},{$limit:5}])')
d5 = list(products.aggregate([
    {"$unwind": "$recent_reviews"},
    {"$group": {"_id": "$seller.state", "reviews": {"$sum": 1},
                "one_star": {"$sum": {"$cond": [{"$eq": ["$recent_reviews.rating", 1]}, 1, 0]}}}},
    {"$sort": {"reviews": -1}}, {"$limit": 5}]))
for g in d5:
    print(f"  {g['_id']:<10} reviews={g['reviews']:>5}  one_star={g['one_star']:>4}  ({100 * g['one_star'] / g['reviews']:.1f}%)")

# Update -----------------------------------------------------------------------
banner("U1  atomic in-document update when a new review arrives (bounded embedded array of 3)",
       'db.products.updateOne({_id:"KSW-000028"},{$push:{recent_reviews:{$each:[{...}],$position:0,$slice:3}},'
       '$inc:{"rating.count":1}})')
before = products.find_one({"_id": "KSW-000028"}, {"rating": 1, "recent_reviews": 1})
products.update_one({"_id": "KSW-000028"},
                    {"$push": {"recent_reviews": {"$each": [{"customer_id": 777, "rating": 5, "text": "Fast delivery to Port Harcourt",
                                                             "date": "2026-11-01"}], "$position": 0, "$slice": 3}},
                     "$inc": {"rating.count": 1}})
after = products.find_one({"_id": "KSW-000028"}, {"rating": 1, "recent_reviews": 1})
print(f"  rating.count {before['rating']['count']} -> {after['rating']['count']}; "
      f"recent_reviews length {len(before['recent_reviews'])} -> {len(after['recent_reviews'])}; newest = {after['recent_reviews'][0]['text']!r}")

# Cross-check with PostgreSQL JSONB ----------------------------------------------
print("\n" + "=" * 90 + "\nCross-check: MongoDB-API results vs PostgreSQL JSONB results\n" + "-" * 90)
cur.execute("""SELECT sku FROM kasuwa_doc.product_catalogue
               WHERE doc @> '{"category":{"name":"Mobile Phones"},"attributes":{"network":["5G"]},"active":true}'
               AND (doc#>>'{attributes,ram_gb}')::int >= 8 AND (doc->>'price')::numeric < 500000
               ORDER BY (doc#>>'{rating,avg}')::numeric DESC, sku LIMIT 5""")
print(f"  D1 same 5 products in same order: {[d['_id'] for d in d1] == [r[0] for r in cur.fetchall()]}")
cur.execute("""SELECT doc#>>'{attributes,brand}', count(*) FROM kasuwa_doc.product_catalogue
               WHERE doc @> '{"category":{"name":"Mobile Phones"}}' GROUP BY 1""")
print(f"  D2 same brand counts: {sorted((g['_id'], g['phones']) for g in d2) == sorted(cur.fetchall())}")
cur.execute("""SELECT count(*) FROM kasuwa_doc.product_catalogue
               WHERE doc @> '{"category":{"name":"Women''s Clothing"},"attributes":{"sizes":["12"],"colours":["Green"]}}'""")
print(f"  D3 same count: {d3 == cur.fetchone()[0]}")
