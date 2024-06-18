def test_render_query(snowflake_engine):
    results = snowflake_engine.generate_sql("""select pi;""")[0]

    assert "3.14" in results

    results2 = snowflake_engine.generate_sql(
        """
        const today <- date_part(current_datetime() , day);
        const ten_days_from_now <- date_add(current_datetime() , day, 10);
        const ten_day_diff <- date_diff(today, ten_days_from_now, day);
        select 
            today,
            ten_days_from_now,
            ten_day_diff;"""
    )[0]
    assert "date_add(current_datetime(),day" in results2, results2
