Extract records from the HTML below against the given schema.

Return one object per record that the page actually contains. Use the text as it
appears on the page; do not normalise, reformat or complete values. A field the
page does not carry is null, never a guess. If the fragment contains no records,
return an empty list.
