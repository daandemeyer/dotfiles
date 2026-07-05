function music --description 'Shuffle live sets from favorite artists'
    set -l artists \
                anjunadeep \
                "lane 8" \
                "ben böhmer" \
                "tinlicker" \
                "eli and fur" \
                "marsh" \
                "jan blomqvist" \
                "james grant" \
		"cri" \
		"my friend" \
                "simon doty" 
    
    set -l urls
    
    for artist in $artists
        set -a urls (yt-dlp -4 "ytsearch15:$artist live set" \
                            --flat-playlist \
                            --print webpage_url)
    end
    
    set -a urls (yt-dlp -4 "ytsearch15:tony mcguinness above beyond deep warm up set" \
                    --flat-playlist \
                    --print webpage_url)
    
    mpv --no-video --ytdl-format=bestaudio --ytdl-raw-options=force-ipv4= \
                --shuffle --loop-playlist=inf $urls
end
