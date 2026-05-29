# Generated for Data Lab UI language preference

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0011_user_custom_hotkeys"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="ui_locale",
            field=models.CharField(
                default="en",
                help_text="Preferred language for the web UI (e.g. en, zh-Hans).",
                max_length=32,
                verbose_name="UI locale",
            ),
        ),
    ]
