from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("carts", "0001_initial")]

    operations = [
        migrations.AddField(model_name="hold", name="guest_key", field=models.CharField(blank=True, db_index=True, default="", max_length=64)),
        migrations.AddField(model_name="cart", name="guest_key", field=models.CharField(blank=True, db_index=True, default="", max_length=64)),
    ]
