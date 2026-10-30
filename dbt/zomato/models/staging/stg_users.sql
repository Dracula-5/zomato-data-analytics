-- One row per registered user. The plain-text `password` column (classified `secret`)
-- never reaches this layer: ingestion drops it, so no copy exists in the lake or warehouse.
-- Source headers have capitals and spaces ("Marital Status"); they are renamed here.
with source as (
    select * from {{ source('raw', 'users') }}
)

select
    try_cast(user_id as bigint)                               as user_id,
    trim(name)                                                as full_name,
    lower(trim(email))                                        as email,
    cast(try_cast("Age" as double) as integer)                as age,
    "Gender"                                                  as gender,
    "Marital Status"                                          as marital_status,
    "Occupation"                                              as occupation,
    "Monthly Income"                                          as monthly_income,
    "Educational Qualifications"                              as education,
    cast(try_cast("Family size" as double) as integer)        as family_size,
    _batch_id,
    _loaded_at
from source
