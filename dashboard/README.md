# Pulso TransMi dashboard

Dashboard bonus desplegable en Vercel. La raíz del proyecto debe ser `dashboard`.

Configura únicamente como variables privadas de Vercel:

- `PULSO_API_URL`
- `PULSO_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`

La llave de Pulso y la service role key solo se usan en `app/api/dashboard/route.ts`.
No uses el prefijo `NEXT_PUBLIC_` para ninguna de ellas.

El panel calcula drift como el cambio relativo de la media de demanda de los
últimos siete días frente a los siete días anteriores. La accuracy y cobertura
se calculan sobre predicciones con `actual_value`; cuando no hay ground truth,
el panel muestra que aún no existe evidencia y no inventa un porcentaje.
