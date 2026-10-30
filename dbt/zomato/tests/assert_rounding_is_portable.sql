-- Fails if a rate rounds twice. Snowflake's NUMBER division kept 6 decimals, turning
-- 753 / 12314 = 0.06114991 into 0.06115 and then 0.0612, while DuckDB gave 0.0611.
-- 3.225 is an exact tie: both warehouses must send it the same way.
select 'ratio' as failed_check
where {{ ratio('753', '12314', 4) }} <> 0.0611

union all

select 'tie'
where {{ round_double('3.225', 2) }} <> 3.23
