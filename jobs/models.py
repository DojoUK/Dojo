from django.db import models


class JobRun(models.Model):
    """When each scheduled job last ran, so Settings can show the scheduler is alive."""
    name = models.CharField(max_length=100, primary_key=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    last_finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self):
        return self.name
