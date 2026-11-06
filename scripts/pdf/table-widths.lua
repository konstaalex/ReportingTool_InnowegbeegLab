-- Pandoc 2.9 filter: give pipe tables column widths proportional to their content,
-- so long cells wrap in wide columns instead of every column getting the same width.
-- A column is never narrower than its longest unbreakable word (e.g. a file path).
local function cell_text(cell)
  return pandoc.utils.stringify(pandoc.Div(cell))
end

function Table(el)
  local n = #el.aligns
  if n == 0 then return nil end
  local maxlen, maxword = {}, {}
  for i = 1, n do maxlen[i] = 4; maxword[i] = 4 end
  local function scan(row)
    for i, cell in ipairs(row) do
      local t = cell_text(cell)
      if #t > maxlen[i] then maxlen[i] = #t end
      for w in t:gmatch("%S+") do
        if #w > maxword[i] then maxword[i] = #w end
      end
    end
  end
  scan(el.headers)
  for _, row in ipairs(el.rows) do scan(row) end
  local weights, total = {}, 0
  for i = 1, n do
    weights[i] = math.max(math.sqrt(math.min(maxlen[i], 400)) + 1.5, maxword[i] * 0.42)
    total = total + weights[i]
  end
  for i = 1, n do el.widths[i] = weights[i] / total * 0.98 end
  return el
end
