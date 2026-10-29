-- One row per registered user. The plain-text `password` column (classified `secret`)
-- is deliberately never selected, so it cannot reach any model downstream.
with source as (
    select * from {{ source('raw', 'users') }}
)

select
    try_cast(user_id as bigint)                               as user_id,
    trim(name)                                                as full_name,
    lower(trim(email))                                        as email,
    cast(try_cast(age as double) as integer)                  as age,
    gender,
    marital_status,
    occupation,
    monthly_income,
    education,
    cast(try_cast(family_size as double) as integer)          as family_size,
    _batch_id,
    _loaded_at
from source
