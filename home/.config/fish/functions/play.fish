function play --description 'Play YouTube URLs as audio only'
    if test (count $argv) -eq 0
        echo "Usage: play URL..." >&2
        return 1
    end

    mpv --no-video --ytdl-format=bestaudio --ytdl-raw-options=force-ipv4= $argv
end
