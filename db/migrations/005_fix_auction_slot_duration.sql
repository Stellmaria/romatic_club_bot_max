-- 005_fix_auction_slot_duration.sql
-- Слот публикации занимает 30 минут, а приём ставок продолжается до
-- последней секунды отображаемой конечной минуты: start_time + 30:59.
-- Ранее триггер выставлял +31:00, из-за чего соседние слоты на сетке
-- каждые 30 минут ошибочно пересекались.

SET search_path = public, pg_catalog;

-- На время нормализации отключаем защитные триггеры. Иначе старые строки
-- со ставками нельзя исправить даже миграцией.
DROP TRIGGER IF EXISTS trg_prevent_time_change ON public.auctions;
DROP TRIGGER IF EXISTS trg_auctions_fix_end_time ON public.auctions;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_31;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_31_window;

ALTER TABLE public.auctions
    DROP CONSTRAINT IF EXISTS auctions_end_eq_start_plus_30m59s;

CREATE OR REPLACE FUNCTION public.auctions_fix_end_time()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.end_time := NEW.start_time + INTERVAL '30 minutes 59 seconds';
    RETURN NEW;
END
$function$;

-- Приводим существующие записи к тому же правилу. Это не меняет номер
-- получасового слота, но убирает ложное пересечение с соседним слотом.
UPDATE public.auctions
SET end_time = start_time + INTERVAL '30 minutes 59 seconds'
WHERE end_time IS DISTINCT FROM start_time + INTERVAL '30 minutes 59 seconds';

ALTER TABLE public.auctions
    ADD CONSTRAINT auctions_end_eq_start_plus_30m59s
    CHECK (
        abs(
            extract(
                epoch FROM (
                    end_time - (start_time + INTERVAL '30 minutes 59 seconds')
                )
            )
        ) <= 2
    );

CREATE TRIGGER trg_auctions_fix_end_time
    BEFORE INSERT OR UPDATE OF start_time ON public.auctions
    FOR EACH ROW
    EXECUTE FUNCTION public.auctions_fix_end_time();

CREATE TRIGGER trg_prevent_time_change
    BEFORE UPDATE ON public.auctions
    FOR EACH ROW
    EXECUTE FUNCTION public.prevent_time_change_if_bids();
