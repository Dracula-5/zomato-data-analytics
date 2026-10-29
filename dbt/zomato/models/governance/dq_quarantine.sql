-- Records held back from the facts because they broke a rule with action = 'quarantine'.
-- They stay here, with the reason, so a data steward can fix or release them.
select
    table_name,
    record_key,
    rule_id,
    detail,
    current_timestamp as quarantined_at
from {{ ref('int_dq_violations') }}
where action = 'quarantine'
