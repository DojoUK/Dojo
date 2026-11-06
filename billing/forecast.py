"""A simple trend projection for the finance report's charts (display only, not accounting)."""


def linear_forecast(values, periods_ahead):
    """
    Fit a least-squares straight line through `values` (one per month, oldest
    first) and extend it `periods_ahead` months. With fewer than two months
    of history the last value is repeated. Never projects below zero.
    """
    n = len(values)
    if n == 0:
        return [0.0] * periods_ahead
    if n < 2:
        return [round(max(0.0, float(values[0])), 2)] * periods_ahead
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in enumerate(values))
    denominator = sum((x - x_mean) ** 2 for x in range(n))
    slope = numerator / denominator if denominator else 0
    intercept = y_mean - slope * x_mean
    return [round(max(0.0, intercept + slope * (n + i)), 2) for i in range(periods_ahead)]


def next_months(last_month, count):
    """The first day of each of the `count` months after `last_month`."""
    from datetime import date
    months = []
    year, month = last_month.year, last_month.month
    for _ in range(count):
        month += 1
        if month > 12:
            year, month = year + 1, 1
        months.append(date(year, month, 1))
    return months
