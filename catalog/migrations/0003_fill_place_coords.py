from django.db import migrations

# City centres (WGS84). Station/venue places inherit their city's coordinates.
CITY_COORDS = {
    "Kathmandu": (27.7172, 85.3240),
    "Pokhara": (28.2096, 83.9856),
    "Beni": (28.3500, 83.5667),
    "Bhairahawa": (27.5000, 83.4500),
    "Janakpur": (26.7288, 85.9266),
    "Jayanagar": (26.5833, 85.9167),
}


def fill(apps, schema_editor):
    Place = apps.get_model("catalog", "Place")
    for city, (lat, lng) in CITY_COORDS.items():
        Place.objects.filter(city=city, latitude__isnull=True).update(latitude=lat, longitude=lng)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_place_coords_vehicle_position")]
    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
