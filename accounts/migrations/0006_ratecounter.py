from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_alter_user_role'),
    ]

    operations = [
        migrations.CreateModel(
            name='RateCounter',
            fields=[
                ('key', models.CharField(max_length=255, primary_key=True, serialize=False)),
                ('count', models.PositiveIntegerField(default=0)),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
        ),
    ]
